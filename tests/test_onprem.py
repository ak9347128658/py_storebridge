"""On-premises provider: local disk, signed HTTP, and install decisions."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import pytest

from py_storebridge import (
    FileNotFoundError,
    OnPremEngine,
    ProviderError,
    UploadOptions,
    ValidationError,
    Visibility,
    create_storage,
    parse_storage_config,
    transfer,
)
from py_storebridge.config import OnPremCredentials
from py_storebridge.providers.onprem import OnPremProvider
from py_storebridge.providers.onprem_install import (
    bootstrap_minio,
    endpoint_ready,
    minio_release_url,
    plan_install,
    resolve_onprem,
)
from py_storebridge.providers.onprem_sign import sign, verify


def _split_server(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    assert parsed.hostname is not None and parsed.port is not None
    return parsed.hostname, parsed.port


def _inside(child: Path, parent: Path) -> bool:
    child_s = os.path.normcase(str(child.resolve()))
    parent_s = os.path.normcase(str(parent.resolve()))
    return child_s == parent_s or child_s.startswith(parent_s + os.sep)


def _storage(tmp_path: Path, **credentials):
    payload = {"bucket": "app", "root": str(tmp_path), "engine": "filesystem"}
    payload.update(credentials)
    client = create_storage(
        {
            "provider": "onprem",
            "credentials": payload,
            "log_level": "silent",
            "retry": {"max_retries": 0},
        }
    )
    return client


def test_empty_onprem_config_defaults_to_auto_filesystem() -> None:
    parsed = parse_storage_config({"provider": "onprem"})
    assert parsed.provider == "onprem"
    assert parsed.credentials.engine == OnPremEngine.AUTO
    assert parsed.credentials.bucket == "default"
    assert parsed.credentials.install is False
    assert parsed.credentials.allow_download is False


def test_onprem_accepts_s3_style_key_names() -> None:
    parsed = parse_storage_config(
        {
            "provider": "onprem",
            "credentials": {
                "bucket": "app",
                "endPoint": "minio.internal",
                "accessKeyId": "AKI",
                "secretAccessKey": "SECRET",
                "useSSL": False,
                "installMethod": "docker",
            },
        }
    )
    assert parsed.credentials.access_key == "AKI"
    assert parsed.credentials.secret_key == "SECRET"
    assert parsed.credentials.end_point == "minio.internal"
    assert parsed.credentials.install_method == "docker"


def test_onprem_rejects_bad_bucket_and_short_secret() -> None:
    with pytest.raises(ValidationError):
        parse_storage_config({"provider": "onprem", "credentials": {"bucket": "Bad_Name"}})
    with pytest.raises(ValidationError):
        parse_storage_config(
            {"provider": "onprem", "credentials": {"bucket": "app", "signing_secret": "too-short"}}
        )


def test_filesystem_private_and_public_roundtrip(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    try:
        private = storage.upload(
            key="users/profile.png",
            file=b"private-bytes",
            content_type="image/png",
            visibility="private",
            metadata={"owner": "ada"},
        )
        assert private.provider == "onprem"
        assert private.etag == hashlib.md5(b"private-bytes").hexdigest()
        assert storage.get_buffer("users/profile.png") == b"private-bytes"
        meta = storage.get_metadata("users/profile.png")
        assert meta.visibility == Visibility.PRIVATE
        assert meta.metadata == {"owner": "ada"}
        assert meta.content_type == "image/png"

        storage.upload(key="assets/logo.png", file=b"public-bytes", visibility="public")
        assert storage.get_metadata("assets/logo.png").visibility == Visibility.PUBLIC
        assert storage.exists("assets/logo.png") is True
        assert storage.exists("missing.png") is False
        storage.delete(key="does-not-exist.png")
        storage.delete(key="assets/logo.png")
        assert storage.exists("assets/logo.png") is False
        with pytest.raises(FileNotFoundError):
            storage.get_metadata("assets/logo.png")
    finally:
        storage.provider.close()


def test_filesystem_encodes_windows_and_unicode_keys(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    try:
        keys = ["con/aux.txt", "sb.hello/x", "a b/c:d?.txt", "用户/文件.txt"]
        for key in keys:
            storage.upload(key=key, file=key.encode("utf-8"))
            assert storage.get_buffer(key) == key.encode("utf-8")
        listed = {entry.key for entry in storage.iter_files()}
        assert set(keys).issubset(listed)
        root = Path(storage.provider.root).resolve()
        for path in (root / "buckets" / "app" / "objects").rglob("*"):
            if path.is_file():
                assert _inside(path, root)
    finally:
        storage.provider.close()


def test_filesystem_rejects_parent_segments(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    try:
        with pytest.raises(ValidationError):
            storage.upload(key="../secret", file=b"nope")
        with pytest.raises(ValidationError):
            storage.upload(key="a/../../etc/passwd", file=b"nope")
    finally:
        storage.provider.close()


def test_list_prefix_delimiter_and_cursor(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    try:
        for key in ("dir/a.txt", "dir/b.txt", "dir/sub/c.txt", "other.txt"):
            storage.upload(key=key, file=b"x")
        page = storage.list(prefix="dir/", delimiter="/")
        assert [entry.key for entry in page.files] == ["dir/a.txt", "dir/b.txt"]
        assert page.prefixes == ["dir/sub/"]

        seen = []
        cursor = None
        while True:
            current = storage.list(prefix="dir/", limit=1, cursor=cursor)
            seen.extend(entry.key for entry in current.files)
            if not current.next_cursor:
                break
            cursor = current.next_cursor
        assert seen == ["dir/a.txt", "dir/b.txt", "dir/sub/c.txt"]
    finally:
        storage.provider.close()


def test_copy_move_and_multipart(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    try:
        storage.upload(key="original.txt", file=b"abcdefghij", visibility="private")
        copied = storage.copy(source_key="original.txt", destination_key="copies/one.txt", visibility="public")
        assert copied.key == "copies/one.txt"
        assert storage.get_metadata("copies/one.txt").visibility == Visibility.PUBLIC
        assert storage.get_buffer("original.txt") == b"abcdefghij"
        storage.move(source_key="original.txt", destination_key="moved.txt")
        assert storage.exists("original.txt") is False
        assert storage.get_buffer("moved.txt") == b"abcdefghij"

        payload = b"abcdefghij"
        stored = storage.upload_multipart({"key": "big.bin", "file": payload}, chunk_size=4)
        assert stored.size == len(payload)
        assert storage.get_buffer("big.bin") == payload
    finally:
        storage.provider.close()


def test_signing_secret_is_reused_for_the_same_root(tmp_path: Path) -> None:
    first = _storage(tmp_path)
    second = _storage(tmp_path)
    try:
        assert first.provider.signing_secret == second.provider.signing_secret
        assert (tmp_path / ".signing_secret").is_file()
        url = first.get_signed_url("users/profile.png", expires_in=60)
        assert "sb-signature=" in url
    finally:
        first.provider.close()
        second.provider.close()


def test_signature_rejects_tampering_and_expiry() -> None:
    secret = "x" * 32
    good = sign(secret, "GET", "app", "a.txt", 2_000_000_000)
    query = {"sb-expires": ["2000000000"], "sb-signature": [good]}
    assert verify(secret, "GET", "app", "a.txt", query, now=1_000) is None
    assert verify(secret, "PUT", "app", "a.txt", query, now=1_000) == "invalid signature"
    assert verify(secret, "GET", "app", "a.txt", query, now=2_000_000_000) == "expired"
    flipped = good[:-1] + ("0" if good[-1] != "0" else "1")
    assert verify(secret, "GET", "app", "a.txt", {"sb-expires": "2000000000", "sb-signature": flipped}, now=1_000)


def test_embedded_server_public_private_and_signed_writes(tmp_path: Path) -> None:
    storage = _storage(
        tmp_path,
        serve=True,
        serve_port=0,
        signing_secret="signing-secret-value",
        cors_origin="*",
    )
    try:
        storage.upload(key="assets/logo.txt", file=b"logo", visibility="public", content_type="text/plain")
        storage.upload(key="users/secret.txt", file=b"secret", visibility="private")
        public_url = storage.get_url("assets/logo.txt")
        with urlopen(public_url) as response:
            assert response.status == 200
            assert response.read() == b"logo"

        private_url = storage.get_url("users/secret.txt")
        with pytest.raises(HTTPError) as missing:
            urlopen(private_url)
        assert missing.value.code == 404

        signed = storage.get_signed_url("users/secret.txt", expires_in=120)
        with urlopen(signed) as response:
            assert response.read() == b"secret"

        tampered = signed[:-1] + ("0" if signed[-1] != "0" else "1")
        with pytest.raises(HTTPError) as denied:
            urlopen(tampered)
        assert denied.value.code == 403

        put_url = storage.get_signed_url("incoming.txt", method="PUT", expires_in=120, content_type="text/plain")
        request = Request(
            put_url,
            data=b"put-body",
            method="PUT",
            headers={"Content-Type": "text/plain"},
        )
        with urlopen(request) as response:
            assert response.status == 201
        assert storage.get_buffer("incoming.txt") == b"put-body"
        assert storage.get_metadata("incoming.txt").visibility == Visibility.PRIVATE

        delete_url = storage.get_signed_url("incoming.txt", method="DELETE", expires_in=120)
        delete_request = Request(delete_url, method="DELETE")
        with urlopen(delete_request) as response:
            assert response.status == 204
        assert storage.exists("incoming.txt") is False

        host, port = _split_server(storage.get_url("assets/logo.txt"))
        connection = http.client.HTTPConnection(host, port, timeout=3)
        connection.request("GET", "/app/../../secret")
        escaped = connection.getresponse()
        assert escaped.status == 400
        escaped.read()
        connection.request("GET", "/other/file.txt")
        other = connection.getresponse()
        assert other.status == 404
        other.read()
        connection.request("OPTIONS", "/app/assets/logo.txt")
        options = connection.getresponse()
        assert options.status == 204
        assert options.getheader("Access-Control-Allow-Origin") == "*"
        options.read()
        connection.close()
    finally:
        storage.provider.close()


def test_transfer_between_two_local_roots(tmp_path: Path) -> None:
    source = _storage(tmp_path / "source")
    destination = _storage(tmp_path / "destination")
    try:
        source.upload(key="docs/note.txt", file=b"note", visibility="public")
        result = transfer(source, destination, source_key="docs/note.txt")
        assert result.transferred == 1
        assert destination.get_buffer("docs/note.txt") == b"note"
        assert destination.get_metadata("docs/note.txt").visibility == Visibility.PUBLIC
    finally:
        source.provider.close()
        destination.provider.close()


def test_max_upload_bytes(tmp_path: Path) -> None:
    storage = _storage(tmp_path, max_upload_bytes=4)
    try:
        storage.upload(key="ok.txt", file=b"1234")
        with pytest.raises(ValidationError):
            storage.upload(key="big.txt", file=b"12345")
    finally:
        storage.provider.close()


def test_resolve_engine_decisions(tmp_path: Path) -> None:
    root = str(tmp_path)

    def boom(credentials, endpoint):
        raise AssertionError("installer should not run")

    filesystem = resolve_onprem(
        OnPremCredentials(
            bucket="app",
            root=root,
            engine="filesystem",
            install=True,
            endpoint="http://127.0.0.1:9",
        ),
        probe=lambda endpoint, timeout: True,
        installer=boom,
    )
    assert filesystem.engine == "filesystem"
    assert filesystem.installed is False

    auto = resolve_onprem(
        OnPremCredentials(bucket="app", root=root),
        probe=lambda endpoint, timeout: True,
        installer=boom,
    )
    assert auto.engine == "filesystem"

    reachable = resolve_onprem(
        OnPremCredentials(
            bucket="app",
            root=root,
            endpoint="http://minio.internal:9000",
            access_key="user",
            secret_key="password",
        ),
        probe=lambda endpoint, timeout: True,
        installer=boom,
    )
    assert reachable.engine == "s3"
    assert reachable.installed is False
    assert reachable.endpoint == "http://minio.internal:9000"

    with pytest.raises(ProviderError) as down:
        resolve_onprem(
            OnPremCredentials(
                bucket="app",
                root=root,
                endpoint="http://127.0.0.1:9",
                access_key="user",
                secret_key="password",
            ),
            probe=lambda endpoint, timeout: False,
            installer=boom,
        )
    assert "install=true" in str(down.value)

    calls = []

    def installer(credentials, endpoint):
        calls.append(endpoint)
        return "generated-user", "generated-secret"

    installed = resolve_onprem(
        OnPremCredentials(bucket="app", root=root, install=True),
        probe=lambda endpoint, timeout: False,
        installer=installer,
    )
    assert installed.engine == "s3"
    assert installed.installed is True
    assert calls == ["http://127.0.0.1:9000"]
    assert installed.access_key == "generated-user"

    with pytest.raises(ValidationError):
        resolve_onprem(
            OnPremCredentials(bucket="app", root=root, access_key="only-user"),
            probe=lambda endpoint, timeout: False,
        )


def test_endpoint_ready_treats_other_http_servers_as_up(tmp_path: Path) -> None:
    storage = _storage(tmp_path, serve=True, serve_port=0)
    try:
        origin = storage.provider._backend.base_url
        assert endpoint_ready(origin, 1) is True
        assert endpoint_ready("http://127.0.0.1:9", 0.2) is False
    finally:
        storage.provider.close()


def test_plan_install_and_release_urls() -> None:
    assert plan_install("auto", binary_on_path=True, docker_on_path=True, allow_download=False) == "binary"
    assert plan_install("auto", binary_on_path=False, docker_on_path=True, allow_download=False) == "docker"
    assert plan_install("auto", binary_on_path=False, docker_on_path=False, allow_download=True) == "download"
    with pytest.raises(ProviderError) as missing:
        plan_install("auto", binary_on_path=False, docker_on_path=False, allow_download=False)
    assert "filesystem" in str(missing.value)
    with pytest.raises(ProviderError):
        plan_install("none", binary_on_path=True, docker_on_path=True, allow_download=True)
    assert minio_release_url("windows", "amd64").endswith("windows-amd64/minio.exe")
    assert "linux-amd64/minio" in minio_release_url("linux", "x86_64")
    assert "darwin-arm64/minio" in minio_release_url("darwin", "arm64")


def test_bootstrap_uses_existing_binary_and_reuses_saved_keys(tmp_path: Path) -> None:
    credentials = OnPremCredentials(bucket="app", root=str(tmp_path), install=True, startup_timeout=2)
    state = {"up": False, "popens": 0}

    def probe(endpoint, timeout):
        return state["up"]

    def which(name):
        return "C:/tools/minio.exe" if name == "minio" else None

    def popen(args, env, log_handle):
        state["popens"] += 1
        state["up"] = True
        assert args[0] == "C:/tools/minio.exe"
        assert env["MINIO_ROOT_USER"]
        assert len(env["MINIO_ROOT_PASSWORD"]) >= 8

        class _Proc:
            pid = 4242

            def poll(self):
                return None

        return _Proc()

    access, secret = bootstrap_minio(
        credentials,
        "http://127.0.0.1:9000",
        probe=probe,
        which=which,
        popen=popen,
        sleeper=lambda _seconds: None,
    )
    assert state["popens"] == 1
    saved = json.loads((Path(tmp_path).resolve() / "engine.json").read_text(encoding="utf-8"))
    assert saved["access_key"] == access
    assert saved["secret_key"] == secret

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("MinIO is already up")

    again_access, again_secret = bootstrap_minio(
        OnPremCredentials(bucket="app", root=str(tmp_path), install=True),
        "http://127.0.0.1:9000",
        probe=lambda endpoint, timeout: True,
        which=fail_popen,
        popen=fail_popen,
        sleeper=lambda _seconds: None,
    )
    assert (again_access, again_secret) == (access, secret)


def test_bootstrap_refuses_remote_host_and_missing_tools(tmp_path: Path) -> None:
    credentials = OnPremCredentials(bucket="app", root=str(tmp_path), install=True)
    with pytest.raises(ProviderError) as remote:
        bootstrap_minio(
            credentials,
            "http://storage.example:9000",
            probe=lambda endpoint, timeout: False,
            which=lambda name: None,
        )
    assert "localhost" in str(remote.value)

    with pytest.raises(ProviderError) as missing:
        bootstrap_minio(
            credentials,
            "http://127.0.0.1:9000",
            probe=lambda endpoint, timeout: False,
            which=lambda name: None,
            sleeper=lambda _seconds: None,
        )
    assert "allow_download" in str(missing.value)


def test_bootstrap_docker_and_download_are_explicit(tmp_path: Path) -> None:
    commands = []
    state = {"up": False}

    def run(args):
        commands.append(args)
        if "run" in args:
            state["up"] = True
        return 0, ""

    docker_credentials = OnPremCredentials(
        bucket="app",
        root=str(tmp_path / "docker"),
        install=True,
        install_method="docker",
        startup_timeout=2,
    )
    bootstrap_minio(
        docker_credentials,
        "http://127.0.0.1:9000",
        probe=lambda endpoint, timeout: state["up"],
        which=lambda name: "docker" if name == "docker" else None,
        run=run,
        popen=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("docker path must not spawn a binary")),
        sleeper=lambda _seconds: None,
    )
    assert any(item[:2] == ["docker", "run"] for item in commands)
    assert all(not str(item[0]).endswith("minio") for item in commands)

    downloaded = []

    def download(url, dest: Path):
        downloaded.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"minio")

    def popen(args, env, log_handle):
        state["binary"] = args[0]

        class _Proc:
            pid = 7

            def poll(self):
                return None

        return _Proc()

    binary_state = {"up": False}

    def binary_probe(endpoint, timeout):
        return binary_state["up"]

    def binary_popen(args, env, log_handle):
        binary_state["up"] = True
        return popen(args, env, log_handle)

    bootstrap_minio(
        OnPremCredentials(
            bucket="app",
            root=str(tmp_path / "binary"),
            install=True,
            allow_download=True,
            minio_binary_url="https://downloads.example/minio",
            startup_timeout=2,
        ),
        "http://127.0.0.1:9000",
        probe=binary_probe,
        which=lambda name: None,
        download=download,
        popen=binary_popen,
        sleeper=lambda _seconds: None,
    )
    assert downloaded == ["https://downloads.example/minio"]


class _FakeS3:
    def __init__(self) -> None:
        self.objects = {}
        self.buckets = set()

    def head_bucket(self, Bucket, **kwargs):
        self.buckets.add(Bucket)
        return {}

    def create_bucket(self, Bucket, **kwargs):
        self.buckets.add(Bucket)
        return {}

    def put_object(self, Bucket, Key, Body, **extra):
        data = Body if isinstance(Body, bytes) else Body.read()
        self.objects[Key] = {"body": data, "extra": extra}
        return {"ETag": '"abc"'}

    def head_object(self, Bucket, Key):
        item = self.objects[Key]
        metadata = item["extra"].get("Metadata") or {}
        return {
            "ContentLength": len(item["body"]),
            "ContentType": item["extra"].get("ContentType"),
            "Metadata": {key.lower(): value for key, value in metadata.items()},
            "LastModified": datetime.now(timezone.utc),
            "ETag": '"abc"',
        }


def test_s3_engine_records_visibility_without_calling_installer(tmp_path: Path) -> None:
    client = _FakeS3()

    def installer(credentials, endpoint):
        raise AssertionError("reachable service must not be installed")

    provider = OnPremProvider(
        OnPremCredentials(
            bucket="app",
            root=str(tmp_path),
            endpoint="http://127.0.0.1:9000",
            access_key="user",
            secret_key="password",
        ),
        log_level="silent",
        client=client,
        probe=lambda endpoint, timeout: True,
        installer=installer,
    )
    try:
        result = provider.upload(
            UploadOptions(
                key="docs/a.txt",
                file=b"hello",
                content_type="text/plain",
                metadata={"owner": "ada"},
            )
        )
        assert result.provider == "onprem"
        assert result.metadata == {"owner": "ada"}
        stored = client.objects["docs/a.txt"]["extra"]
        assert stored["Metadata"]["storebridge-visibility"] == "private"
        assert "ACL" not in stored
        meta = provider.get_metadata("docs/a.txt")
        assert meta.visibility == Visibility.PRIVATE
        assert meta.metadata == {"owner": "ada"}
        assert provider.engine == "s3"
        assert "app" in client.buckets
    finally:
        provider.close()
