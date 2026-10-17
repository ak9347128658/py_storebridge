"""Choose an on-premises engine and, when asked, bootstrap MinIO.

Decision order for ``engine='auto'``:

* Filesystem when no endpoint is configured and ``install`` is false.
  Nothing is downloaded and no port is opened.
* An existing S3-compatible service when an endpoint is configured and reachable.
  This covers MinIO, Ceph RGW, Garage, and SeaweedFS.
* A bootstrapped MinIO when ``install`` is true and the endpoint is down.
  ``install_method='auto'`` uses a MinIO binary already on PATH, then Docker,
  then an official binary download only when ``allow_download`` is true.

``engine='filesystem'`` never installs a service. ``engine='minio'`` or
``engine='s3'`` never silently falls back to disk: a down endpoint is an error
unless ``install`` is true.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from py_storebridge.config import OnPremCredentials, OnPremEngine, OnPremInstallMethod
from py_storebridge.exceptions import ProviderError, ValidationError

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


@dataclass(frozen=True)
class OnPremPlan:
    engine: str
    endpoint: Optional[str]
    access_key: Optional[str]
    secret_key: Optional[str]
    region: str
    root: str
    installed: bool
    detail: str


def default_root() -> Path:
    configured = os.environ.get("PY_STOREBRIDGE_HOME")
    if configured:
        return Path(configured).expanduser()
    return Path.cwd() / ".py_storebridge"


def resolve_root(credentials: OnPremCredentials) -> Path:
    raw = credentials.root or default_root()
    return Path(raw).expanduser().resolve()


def build_endpoint(credentials: OnPremCredentials, *, default_port: int = 9000) -> Optional[str]:
    if credentials.endpoint:
        return str(credentials.endpoint).rstrip("/")
    host = credentials.end_point
    if not host:
        return None
    use_ssl = bool(credentials.use_ssl)
    scheme = "https" if use_ssl else "http"
    port = credentials.port if credentials.port is not None else default_port
    if (scheme == "https" and port == 443) or (scheme == "http" and port == 80):
        return f"{scheme}://{host}"
    return f"{scheme}://{host}:{port}"


def split_endpoint(endpoint: str) -> tuple[str, str, int]:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationError("endpoint must be an http:// or https:// URL", field="endpoint")
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.hostname, port, parsed.scheme == "https"


def tcp_probe(endpoint: str, timeout: float) -> bool:
    host, port, _ = split_endpoint(endpoint)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _local_https_context() -> ssl.SSLContext:
    # Local MinIO often uses a certificate this process did not issue.
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def endpoint_ready(endpoint: str, timeout: float) -> bool:
    """Wait until a just-started MinIO can serve, without rejecting other S3 servers.

    MinIO answers ``/minio/health/live``. Ceph, Garage, and SeaweedFS usually
    return 4xx for that path, which still means something is accepting HTTP.
    Connection failures mean the process is not ready yet.
    """
    if not tcp_probe(endpoint, timeout):
        return False
    url = endpoint.rstrip("/") + "/minio/health/live"
    context = _local_https_context() if endpoint.startswith("https://") else None
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=context) as response:
            return int(getattr(response, "status", 200)) < 500
    except urllib.error.HTTPError as exc:
        return exc.code < 500
    except Exception:
        return False


def _enum(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)


def minio_release_url(system: Optional[str] = None, machine: Optional[str] = None) -> str:
    system_name = (system or platform.system()).lower()
    machine_name = (machine or platform.machine()).lower()
    if machine_name in {"x86_64", "amd64"}:
        arch = "amd64"
    elif machine_name in {"aarch64", "arm64"}:
        arch = "arm64"
    else:
        raise ProviderError(
            f"No official MinIO binary is mapped for architecture '{machine_name}'. "
            "Install MinIO yourself or set minio_binary_url.",
            "onprem",
        )
    names = {
        "linux": f"linux-{arch}/minio",
        "darwin": f"darwin-{arch}/minio",
        "windows": f"windows-{arch}/minio.exe",
    }
    if system_name not in names:
        raise ProviderError(
            f"No official MinIO binary is mapped for '{system_name}'. "
            "Install MinIO yourself or use engine='filesystem'.",
            "onprem",
        )
    return f"https://dl.min.io/server/minio/release/{names[system_name]}"


def plan_install(
    method: Any,
    *,
    binary_on_path: bool,
    docker_on_path: bool,
    allow_download: bool,
) -> str:
    """Return ``binary``, ``docker``, or ``download``."""
    chosen = _enum(method)
    if chosen == OnPremInstallMethod.NONE:
        raise ProviderError(
            "install_method is 'none', so py_storebridge will not start a storage service. "
            "Use engine='filesystem' or point endpoint at a service that is already running.",
            "onprem",
        )
    if chosen == OnPremInstallMethod.DOCKER:
        if not docker_on_path:
            raise ProviderError(
                "install_method is 'docker' but the docker executable was not found on PATH.",
                "onprem",
            )
        return "docker"
    if chosen == OnPremInstallMethod.BINARY:
        if binary_on_path:
            return "binary"
        if allow_download:
            return "download"
        raise ProviderError(
            "install_method is 'binary' but minio is not on PATH. "
            "Install the MinIO server binary, or set allow_download=true to fetch the official build.",
            "onprem",
        )
    if binary_on_path:
        return "binary"
    if docker_on_path:
        return "docker"
    if allow_download:
        return "download"
    raise ProviderError(
        "No on-premises storage service is available to install. "
        "Use engine='filesystem' to store files on disk with no extra service, "
        "install MinIO or Docker and set install=true, "
        "or set allow_download=true to fetch the official MinIO binary.",
        "onprem",
    )


def container_name_for(bucket: str, override: Optional[str] = None) -> str:
    if override:
        return override
    cleaned = "".join(char if char.isalnum() or char in ".-" else "-" for char in bucket.lower())
    return f"py-storebridge-{cleaned}"[:63].strip("-") or "py-storebridge"


def docker_run_command(
    *,
    image: str,
    name: str,
    host_port: int,
    console_port: int,
    data_dir: Path,
    access_key: str,
    secret_key: str,
    publish_host: str = "127.0.0.1",
) -> list[str]:
    return [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "-p",
        f"{publish_host}:{host_port}:9000",
        "-p",
        f"{publish_host}:{console_port}:9001",
        "-e",
        f"MINIO_ROOT_USER={access_key}",
        "-e",
        f"MINIO_ROOT_PASSWORD={secret_key}",
        "-v",
        f"{data_dir}:/data",
        image,
        "server",
        "/data",
        "--console-address",
        ":9001",
    ]


def minio_server_command(binary: str, data_dir: Path, host: str, port: int, console_port: int) -> list[str]:
    return [
        binary,
        "server",
        str(data_dir),
        "--address",
        f"{host}:{port}",
        "--console-address",
        f"{host}:{console_port}",
    ]


def _is_local_host(host: str) -> bool:
    return host.lower() in _LOCAL_HOSTS


def _credentials_file(root: Path) -> Path:
    return root / "engine.json"


def _read_saved_keys(root: Path) -> Optional[tuple[str, str]]:
    path = _credentials_file(root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProviderError(f"Could not read {path}: {exc}", "onprem", exc) from exc
    access = str(payload.get("access_key") or "")
    secret = str(payload.get("secret_key") or "")
    if len(access) < 3 or len(secret) < 8:
        raise ProviderError(f"Saved MinIO credentials in {path} are incomplete.", "onprem")
    return access, secret


def _write_saved_keys(root: Path, endpoint: str, access_key: str, secret_key: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    path = _credentials_file(root)
    payload = json.dumps(
        {"endpoint": endpoint, "access_key": access_key, "secret_key": secret_key}
    )
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)


def _require_minio_secret(secret: str) -> None:
    if len(secret) < 8:
        raise ValidationError(
            "MinIO requires a secret_key of at least 8 characters when install=true.",
            field="secret_key",
        )


def _resolve_keys(credentials: OnPremCredentials, root: Path, endpoint: str) -> tuple[str, str, bool]:
    """Return ``(access_key, secret_key, generated)``."""
    if credentials.access_key or credentials.secret_key:
        if not credentials.access_key or not credentials.secret_key:
            raise ValidationError(
                "Provide both access_key and secret_key, or neither to generate them.",
                field="access_key",
            )
        _require_minio_secret(credentials.secret_key)
        return credentials.access_key, credentials.secret_key, False
    saved = _read_saved_keys(root)
    if saved is not None:
        return saved[0], saved[1], False
    import secrets

    access_key = "sb" + secrets.token_hex(8)
    secret_key = secrets.token_urlsafe(24)
    _write_saved_keys(root, endpoint, access_key, secret_key)
    return access_key, secret_key, True


def _default_run(args: list[str]) -> tuple[int, str]:
    completed = subprocess.run(args, capture_output=True, text=True, check=False)
    output = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, output


def _default_popen(args: list[str], env: dict[str, str], log_handle) -> subprocess.Popen:
    kwargs: dict[str, Any] = {
        "args": args,
        "env": env,
        "stdout": log_handle,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if os.name == "nt":
        detached = 0x00000008
        no_window = 0x08000000
        kwargs["creationflags"] = detached | no_window
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(**kwargs)


def _default_download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_suffix(dest.suffix + ".partial")
    try:
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        os.replace(temporary, dest)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    try:
        os.chmod(dest, 0o755)
    except OSError:
        return


def _wait_until_up(
    endpoint: str,
    timeout: float,
    probe: Callable[[str, float], bool],
    process: Any = None,
    log_path: Optional[Path] = None,
    sleeper: Callable[[float], None] = time.sleep,
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process is not None and process.poll() is not None:
            raise ProviderError(
                f"MinIO exited with status {process.returncode}. Log: {log_path}",
                "onprem",
            )
        if probe(endpoint, 0.5):
            return
        sleeper(0.2)
    raise ProviderError(
        f"MinIO did not accept connections at {endpoint} within {timeout:.0f}s. Log: {log_path}",
        "onprem",
    )


def bootstrap_minio(
    credentials: OnPremCredentials,
    endpoint: str,
    *,
    probe: Callable[[str, float], bool] = tcp_probe,
    which: Callable[[str], Optional[str]] = shutil.which,
    run: Callable[[list[str]], tuple[int, str]] = _default_run,
    popen: Callable[..., Any] = _default_popen,
    download: Callable[[str, Path], None] = _default_download,
    sleeper: Callable[[float], None] = time.sleep,
) -> tuple[str, str]:
    """Start MinIO when ``endpoint`` is down. Return the access key and secret."""
    host, port, _ = split_endpoint(endpoint)
    if not _is_local_host(host):
        raise ProviderError(
            f"Refusing to install a storage service on '{host}'. "
            "install=true only starts MinIO on this machine (localhost). "
            "Start the service on that host, or use engine='filesystem'.",
            "onprem",
        )
    root = resolve_root(credentials)
    if probe(endpoint, credentials.probe_timeout):
        if credentials.access_key and credentials.secret_key:
            return credentials.access_key, credentials.secret_key
        saved = _read_saved_keys(root)
        if saved is not None:
            return saved
        raise ProviderError(
            f"A storage service is already listening at {endpoint}, but no credentials were found. "
            "Pass access_key and secret_key.",
            "onprem",
        )
    access_key, secret_key, generated = _resolve_keys(credentials, root, endpoint)

    data_dir = Path(credentials.data_dir).expanduser().resolve() if credentials.data_dir else root / "minio-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    console_port = credentials.console_port or (port + 1 if port < 65535 else 9001)
    binary_on_path = which("minio")
    action = plan_install(
        credentials.install_method,
        binary_on_path=bool(binary_on_path),
        docker_on_path=bool(which("docker")),
        allow_download=bool(credentials.allow_download),
    )
    log_path = root / "minio.log"
    process = None
    if action == "docker":
        name = container_name_for(credentials.bucket, credentials.container_name)
        code, output = run(
            ["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.Names}}\t{{.Status}}"]
        )
        if code != 0:
            raise ProviderError(f"docker ps failed: {output.strip()}", "onprem")
        matched = [
            line for line in output.splitlines()
            if line.split("\t", 1)[0] == name
        ]
        if not matched:
            publish_host = "0.0.0.0" if host == "0.0.0.0" else "127.0.0.1"
            code, output = run(
                docker_run_command(
                    image=credentials.minio_image,
                    name=name,
                    host_port=port,
                    console_port=console_port,
                    data_dir=data_dir,
                    access_key=access_key,
                    secret_key=secret_key,
                    publish_host=publish_host,
                )
            )
            if code != 0:
                raise ProviderError(f"docker run failed: {output.strip()}", "onprem")
        elif not matched[0].split("\t", 1)[-1].lower().startswith("up"):
            code, output = run(["docker", "start", name])
            if code != 0:
                raise ProviderError(f"docker start failed: {output.strip()}", "onprem")
    else:
        if action == "download":
            binary_path = root / "bin" / ("minio.exe" if os.name == "nt" else "minio")
            url = credentials.minio_binary_url or minio_release_url()
            try:
                download(url, binary_path)
            except Exception as exc:
                raise ProviderError(
                    f"Could not download MinIO from {url}: {exc}. "
                    "Install MinIO yourself or set engine='filesystem'.",
                    "onprem",
                    exc,
                ) from exc
            binary = str(binary_path)
        else:
            binary = str(binary_on_path)
        env = os.environ.copy()
        env["MINIO_ROOT_USER"] = access_key
        env["MINIO_ROOT_PASSWORD"] = secret_key
        env["MINIO_ACCESS_KEY"] = access_key
        env["MINIO_SECRET_KEY"] = secret_key
        root.mkdir(parents=True, exist_ok=True)
        log_handle = log_path.open("ab")
        try:
            process = popen(
                minio_server_command(binary, data_dir, host, port, console_port),
                env,
                log_handle,
            )
        except OSError as exc:
            raise ProviderError(f"Could not start MinIO: {exc}", "onprem", exc) from exc
        finally:
            log_handle.close()
        (root / "minio.pid").write_text(str(getattr(process, "pid", "")), encoding="utf-8")

    ready = endpoint_ready if probe is tcp_probe else probe
    _wait_until_up(
        endpoint,
        credentials.startup_timeout,
        ready,
        process=process,
        log_path=log_path,
        sleeper=sleeper,
    )
    if generated:
        _write_saved_keys(root, endpoint, access_key, secret_key)
    return access_key, secret_key


def resolve_onprem(
    credentials: OnPremCredentials,
    *,
    probe: Callable[[str, float], bool] = tcp_probe,
    installer: Optional[Callable[[OnPremCredentials, str], tuple[str, str]]] = None,
) -> OnPremPlan:
    root = resolve_root(credentials)
    region = credentials.region or "us-east-1"
    engine = _enum(credentials.engine)
    endpoint = build_endpoint(credentials)
    install = bool(credentials.install)
    starter = installer or bootstrap_minio

    if engine == OnPremEngine.FILESYSTEM:
        detail = f"Using the local filesystem at {root}."
        if endpoint:
            detail += " The configured endpoint is ignored because engine='filesystem'."
        return OnPremPlan("filesystem", None, None, None, region, str(root), False, detail)

    intends_service = engine in {OnPremEngine.MINIO, OnPremEngine.S3} or bool(
        endpoint or credentials.access_key or credentials.secret_key
    )
    if intends_service and endpoint is None and engine == OnPremEngine.AUTO:
        raise ValidationError(
            "access_key and secret_key need an endpoint or end_point.",
            field="endpoint",
        )

    if engine in {OnPremEngine.MINIO, OnPremEngine.S3} and endpoint is None:
        host = credentials.host or "127.0.0.1"
        port = credentials.port if credentials.port is not None else 9000
        scheme = "https" if credentials.use_ssl else "http"
        endpoint = f"{scheme}://{host}:{port}"

    if intends_service:
        if endpoint is None:
            raise ProviderError("On-premises endpoint is missing.", "onprem")
        reachable = probe(endpoint, credentials.probe_timeout)
        if reachable:
            if not credentials.access_key or not credentials.secret_key:
                raise ProviderError(
                    f"Storage service at {endpoint} is reachable but access_key and secret_key are missing.",
                    "onprem",
                )
            return OnPremPlan(
                "s3",
                endpoint,
                credentials.access_key,
                credentials.secret_key,
                region,
                str(root),
                False,
                f"Using the S3-compatible service at {endpoint}.",
            )
        if not install:
            raise ProviderError(
                f"On-premises storage service is not reachable at {endpoint}. "
                "Set install=true to bootstrap MinIO on this machine, "
                "or set engine='filesystem' to store on local disk without a service.",
                "onprem",
            )
        access_key, secret_key = starter(credentials, endpoint)
        return OnPremPlan(
            "s3",
            endpoint,
            access_key,
            secret_key,
            region,
            str(root),
            True,
            f"Bootstrapped MinIO at {endpoint}. Credentials file: {root / 'engine.json'}",
        )

    if install:
        host = credentials.host or "127.0.0.1"
        port = credentials.port if credentials.port is not None else 9000
        scheme = "https" if credentials.use_ssl else "http"
        endpoint = f"{scheme}://{host}:{port}"
        access_key, secret_key = starter(credentials, endpoint)
        return OnPremPlan(
            "s3",
            endpoint,
            access_key,
            secret_key,
            region,
            str(root),
            True,
            f"No storage service was configured. Bootstrapped MinIO at {endpoint}.",
        )

    return OnPremPlan(
        "filesystem",
        None,
        None,
        None,
        region,
        str(root),
        False,
        f"No on-premises endpoint configured. Using the local filesystem at {root}. "
        "Set endpoint to use MinIO, Ceph, Garage, or SeaweedFS. "
        "Set install=true to bootstrap MinIO.",
    )
