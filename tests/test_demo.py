"""Local checks for the py_storebridge 1.0.0 demo apps.

These tests do not call AWS, Azure, or Google. Cloud clients are validated as
config, and transfer() is exercised with in-memory providers.
"""

from __future__ import annotations

import importlib.util
import io
import os
import py_compile
import sys
import tempfile
import unittest
from pathlib import Path

from py_storebridge import (
    FileListEntry,
    FileMetadata,
    FileNotFoundError as MissingObject,
    ListResult,
    TransferResult,
    UploadResult,
    create_storage_from_provider,
    parse_storage_config,
    transfer,
)
from py_storebridge.providers.base import BaseProvider

ROOT = Path(__file__).resolve().parents[1]
ENV_KEYS = [
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_REGION",
    "AWS_BUCKET",
    "AWS_PUBLIC_BUCKET",
    "AWS_PRIVATE_BUCKET",
    "AWS_SESSION_TOKEN",
    "AWS_ENDPOINT",
    "AWS_CDN_URL",
    "AWS_CUSTOM_DOMAIN",
    "AZURE_ACCOUNT_NAME",
    "AZURE_ACCOUNT_KEY",
    "AZURE_CONNECTION_STRING",
    "AZURE_CONTAINER",
    "AZURE_PUBLIC_CONTAINER",
    "AZURE_PRIVATE_CONTAINER",
    "AZURE_CDN_URL",
    "AZURE_CUSTOM_DOMAIN",
    "GCP_PROJECT_ID",
    "GCP_BUCKET",
    "GCP_PUBLIC_BUCKET",
    "GCP_PRIVATE_BUCKET",
    "GCP_KEY_FILENAME",
    "GCP_CLIENT_EMAIL",
    "GCP_PRIVATE_KEY",
    "GCP_CDN_URL",
    "GCP_CUSTOM_DOMAIN",
]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def setUpModule():
    global aws_public, aws_private, azure_public, azure_private, gcp_public, gcp_private
    aws_public = load_module("cfg_aws_public", ROOT / "aws" / "public_storage.py")
    aws_private = load_module("cfg_aws_private", ROOT / "aws" / "private_storage.py")
    azure_public = load_module("cfg_azure_public", ROOT / "azure" / "public_storage.py")
    azure_private = load_module("cfg_azure_private", ROOT / "azure" / "private_storage.py")
    gcp_public = load_module("cfg_gcp_public", ROOT / "gcp" / "public_storage.py")
    gcp_private = load_module("cfg_gcp_private", ROOT / "gcp" / "private_storage.py")


def load_app(folder: str):
    for name in ("app", "public_storage", "private_storage", "migrate"):
        sys.modules.pop(name, None)
    provider_paths = [str(ROOT / item) for item in ("aws", "azure", "gcp")]
    sys.path[:] = [item for item in sys.path if item not in provider_paths]
    sys.path.insert(0, str(ROOT / folder))
    import app

    return app


class MemoryProvider(BaseProvider):
    def __init__(self, name: str):
        super().__init__()
        self.provider_name = name
        self.objects = {}

    def _upload(self, options):
        data = options.file if isinstance(options.file, bytes) else options.file.read()
        self.objects[options.key] = data
        return UploadResult(
            key=options.key,
            url=f"memory://{self.provider_name}/{options.key}",
            provider=self.provider_name,
            size=len(data),
            content_type=options.content_type,
            etag="memory",
            metadata=options.metadata,
        )

    def _delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def _exists(self, key: str) -> bool:
        return key in self.objects

    def _get_metadata(self, key: str) -> FileMetadata:
        if key not in self.objects:
            raise MissingObject(key, self.provider_name)
        data = self.objects[key]
        return FileMetadata(key=key, size=len(data), content_type="text/plain")

    def _list(self, options=None) -> ListResult:
        prefix = options.prefix if options else None
        files = [
            FileListEntry(key=key, size=len(data))
            for key, data in sorted(self.objects.items())
            if not prefix or key.startswith(prefix)
        ]
        return ListResult(files=files)

    def _get_url(self, key: str) -> str:
        return f"memory://{self.provider_name}/{key}"

    def _get_signed_url(self, key: str, options=None) -> str:
        return self._get_url(key) + "?signed=1"

    def _copy(self, options):
        return self.copy_via_download(options)

    def _get_stream(self, key: str):
        if key not in self.objects:
            raise MissingObject(key, self.provider_name)
        return io.BytesIO(self.objects[key])


class FakeStorage:
    def __init__(self, provider: str):
        self.provider = provider
        self.objects = {}

    def upload(self, **kwargs):
        data = kwargs["file"]
        self.objects[kwargs["key"]] = {
            "data": data,
            "content_type": kwargs.get("content_type") or "application/octet-stream",
        }
        return UploadResult(
            key=kwargs["key"],
            url="https://example.test/" + kwargs["key"],
            provider=self.provider,
            size=len(data),
            content_type=kwargs.get("content_type"),
            etag="etag-1",
            metadata=kwargs.get("metadata"),
        )

    def get_buffer(self, key: str) -> bytes:
        return self.objects[key]["data"]

    def get_metadata(self, key: str) -> FileMetadata:
        item = self.objects[key]
        return FileMetadata(
            key=key,
            size=len(item["data"]),
            content_type=item["content_type"],
            visibility="public",
        )

    def get_url(self, key: str) -> str:
        return "https://example.test/" + key

    def get_signed_url(self, key: str, **kwargs) -> str:
        return "https://example.test/signed/" + key


class ConfigTests(unittest.TestCase):
    def use_env(self, **values):
        saved = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        os.environ.update(values)

        def restore():
            for key in ENV_KEYS:
                os.environ.pop(key, None)
            for key, value in saved.items():
                if value is not None:
                    os.environ[key] = value

        self.addCleanup(restore)

    def test_sources_compile(self):
        for path in ROOT.rglob("*.py"):
            if ".venv" in path.parts or "__pycache__" in path.parts:
                continue
            py_compile.compile(str(path), doraise=True)

    def test_aws_public_and_private_buckets(self):
        self.use_env(
            AWS_ACCESS_KEY_ID="AKIATEST",
            AWS_SECRET_ACCESS_KEY="secret",
            AWS_REGION="us-east-1",
            AWS_PUBLIC_BUCKET="public-bucket",
            AWS_PRIVATE_BUCKET="private-bucket",
        )
        public = parse_storage_config(aws_public.build_config())
        private = parse_storage_config(aws_private.build_config())
        self.assertEqual(public.provider, "s3")
        self.assertEqual(public.visibility, "public")
        self.assertEqual(public.credentials.bucket, "public-bucket")
        self.assertEqual(private.provider, "s3")
        self.assertEqual(private.visibility, "private")
        self.assertEqual(private.credentials.bucket, "private-bucket")

    def test_shared_bucket_fallback(self):
        self.use_env(
            AWS_ACCESS_KEY_ID="AKIATEST",
            AWS_SECRET_ACCESS_KEY="secret",
            AWS_REGION="us-east-1",
            AWS_BUCKET="shared-bucket",
        )
        self.assertEqual(parse_storage_config(aws_public.build_config()).credentials.bucket, "shared-bucket")
        self.assertEqual(parse_storage_config(aws_private.build_config()).credentials.bucket, "shared-bucket")

    def test_azure_requires_a_secret_and_accepts_both_visibilities(self):
        self.use_env(AZURE_ACCOUNT_NAME="acct", AZURE_CONTAINER="uploads")
        self.assertIn("AZURE_ACCOUNT_KEY or AZURE_CONNECTION_STRING", azure_public.missing())
        with self.assertRaises(RuntimeError):
            azure_public.build_config()

        self.use_env(
            AZURE_ACCOUNT_NAME="acct",
            AZURE_ACCOUNT_KEY="key",
            AZURE_PUBLIC_CONTAINER="public-container",
            AZURE_PRIVATE_CONTAINER="private-container",
        )
        public = parse_storage_config(azure_public.build_config())
        private = parse_storage_config(azure_private.build_config())
        self.assertEqual(public.provider, "azure")
        self.assertEqual(public.visibility, "public")
        self.assertEqual(public.credentials.container_name, "public-container")
        self.assertEqual(private.visibility, "private")
        self.assertEqual(private.credentials.container_name, "private-container")

    def test_gcp_email_and_key_file(self):
        self.use_env(
            GCP_PROJECT_ID="proj",
            GCP_BUCKET="shared",
            GCP_CLIENT_EMAIL="sa@proj.iam.gserviceaccount.com",
            GCP_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\\nABC\\n-----END PRIVATE KEY-----\\n",
        )
        private = parse_storage_config(gcp_private.build_config())
        self.assertEqual(private.provider, "gcs")
        self.assertEqual(private.visibility, "private")
        self.assertEqual(private.credentials.bucket, "shared")
        self.assertEqual(private.credentials.credentials.client_email, "sa@proj.iam.gserviceaccount.com")

        handle, name = tempfile.mkstemp(suffix=".json")
        os.close(handle)
        self.addCleanup(os.remove, name)
        self.use_env(GCP_PROJECT_ID="proj", GCP_PUBLIC_BUCKET="pub", GCP_KEY_FILENAME=name)
        public = parse_storage_config(gcp_public.build_config())
        self.assertEqual(public.visibility, "public")
        self.assertEqual(public.credentials.key_filename, str(Path(name).resolve()))
        self.assertIsNone(public.credentials.credentials)


class LibraryTransferTests(unittest.TestCase):
    def test_transfer_keeps_public_and_private_separate(self):
        source = create_storage_from_provider(MemoryProvider("s3"))
        destination = create_storage_from_provider(MemoryProvider("gcs"))
        source.upload(key="public/hello.txt", file=b"hello", content_type="text/plain", visibility="public")
        source.upload(key="private/secret.txt", file=b"secret", content_type="text/plain", visibility="private")

        planned = transfer(
            source,
            destination,
            prefix="public/",
            destination_prefix="public/",
            dry_run=True,
        )
        self.assertEqual(planned.planned, 1)
        self.assertEqual(planned.transferred, 0)
        self.assertFalse(destination.exists("public/hello.txt"))

        copied = transfer(
            source,
            destination,
            prefix="public/",
            destination_prefix="public/",
            preserve_metadata=True,
            preserve_visibility=True,
            concurrency=2,
        )
        self.assertEqual(copied.transferred, 1)
        self.assertEqual(copied.failed, 0)
        self.assertEqual(destination.get_buffer("public/hello.txt"), b"hello")
        self.assertFalse(destination.exists("private/secret.txt"))

        again = transfer(
            source,
            destination,
            prefix="public/",
            destination_prefix="public/",
            skip_existing=True,
        )
        self.assertEqual(again.skipped, 1)
        self.assertEqual(again.transferred, 0)

    def test_aws_migrate_helper_copies_one_prefix(self):
        load_app("aws")
        import migrate as aws_migrate

        source = create_storage_from_provider(MemoryProvider("s3"))
        destination = create_storage_from_provider(MemoryProvider("azure"))
        source.upload(key="public/a.txt", file=b"alpha", content_type="text/plain", visibility="public")
        source.upload(key="private/b.txt", file=b"beta", content_type="text/plain", visibility="private")
        result = aws_migrate._transfer(
            source,
            destination,
            "public",
            source_key=None,
            dry_run=False,
            delete_source=False,
            skip_existing=False,
        )
        self.assertEqual(result.transferred, 1)
        self.assertEqual(destination.get_buffer("public/a.txt"), b"alpha")
        self.assertFalse(destination.exists("private/b.txt"))

        one = aws_migrate._transfer(
            source,
            destination,
            "private",
            source_key="private/b.txt",
            dry_run=False,
            delete_source=False,
            skip_existing=False,
        )
        self.assertEqual(one.transferred, 1)
        self.assertEqual(destination.get_buffer("private/b.txt"), b"beta")


class AppFlowTests(unittest.TestCase):
    def test_upload_stores_json_and_migration_route(self):
        for folder in ("aws", "azure", "gcp"):
            with self.subTest(folder=folder):
                app = load_app(folder)
                directory = tempfile.TemporaryDirectory()
                self.addCleanup(directory.cleanup)
                app.CATALOG = Path(directory.name) / "uploads.json"
                public_store = FakeStorage(app.LIBRARY_PROVIDER)
                private_store = FakeStorage(app.LIBRARY_PROVIDER)
                app.public_client = lambda store=public_store: store
                app.private_client = lambda store=private_store: store
                app.public_missing = lambda: ["EXAMPLE_BUCKET"]
                app.private_missing = lambda: []

                client = app.app.test_client()
                page = client.get("/")
                self.assertEqual(page.status_code, 200)
                self.assertIn(b"Public upload", page.data)
                self.assertIn(b"Private upload", page.data)
                self.assertIn(b"Migrate", page.data)
                self.assertIn(b"preview-image", page.data)
                self.assertNotIn(b"JSON.stringify(record", page.data)

                info = client.get("/api/info").get_json()
                self.assertEqual(info["library"], "py_storebridge 1.0.0")
                self.assertEqual(info["port"], app.PORT)
                self.assertEqual(client.get("/missing").status_code, 404)

                blocked = client.post(
                    "/api/upload",
                    data={"visibility": "public", "file": (io.BytesIO(b"nope"), "notes.txt")},
                )
                self.assertEqual(blocked.status_code, 400)
                self.assertIn("EXAMPLE_BUCKET", blocked.get_json()["error"])

                app.public_missing = lambda: []
                uploaded = client.post(
                    "/api/upload",
                    data={"visibility": "public", "file": (io.BytesIO(b"hello store"), "notes.txt")},
                )
                self.assertEqual(uploaded.status_code, 200, uploaded.get_data(as_text=True))
                record = uploaded.get_json()["record"]
                self.assertTrue(record["read_back_matches"])
                self.assertEqual(record["content_text"], "hello store")
                self.assertEqual(record["provider"], app.LIBRARY_PROVIDER)
                self.assertTrue(record["key"].startswith("public/"))
                self.assertIn("hello store", app.CATALOG.read_text(encoding="utf-8"))
                self.assertEqual(public_store.get_buffer(record["key"]), b"hello store")

                binary = client.post(
                    "/api/upload",
                    data={
                        "visibility": "private",
                        "file": (io.BytesIO(b"\xff\xfe binary"), "blob.bin"),
                    },
                )
                self.assertEqual(binary.status_code, 200, binary.get_data(as_text=True))
                private_record = binary.get_json()["record"]
                self.assertIsNone(private_record["content_text"])
                self.assertTrue(private_record["content_base64"])
                self.assertTrue(private_record["key"].startswith("private/"))
                self.assertIn(private_record["key"], private_store.objects)
                self.assertNotIn(private_record["key"], public_store.objects)

                png = b"\x89PNG\r\n\x1a\npreview"
                image = client.post(
                    "/api/upload",
                    data={
                        "visibility": "public",
                        "file": (io.BytesIO(png), "photo.png", "image/png"),
                    },
                )
                self.assertEqual(image.status_code, 200, image.get_data(as_text=True))
                image_record = image.get_json()["record"]
                self.assertEqual(image_record["content_type"], "image/png")
                self.assertTrue(image_record["content_base64"])
                self.assertIsNone(image_record["content_text"])
                inline = client.get(
                    "/api/download",
                    query_string={"key": image_record["key"], "visibility": "public", "inline": "1"},
                )
                self.assertEqual(inline.status_code, 200)
                self.assertEqual(inline.data, png)
                self.assertTrue(inline.headers["Content-Type"].startswith("image/png"))
                self.assertIn("inline", inline.headers["Content-Disposition"])

                listed = client.get("/api/uploads").get_json()
                self.assertEqual(len(listed["uploads"]), 3)
                read_back = client.get(
                    "/api/read",
                    query_string={"key": record["key"], "visibility": "public"},
                )
                self.assertEqual(read_back.status_code, 200)
                self.assertEqual(read_back.get_json()["content_text"], "hello store")

                calls = []

                def fake_run(target, visibility, **kwargs):
                    calls.append((target, visibility, kwargs))
                    return TransferResult(
                        source_provider=app.LIBRARY_PROVIDER,
                        destination_provider=target,
                        dry_run=True,
                        planned=1,
                    )

                app.migrate.run = fake_run
                migrated = client.post(
                    "/api/migrate",
                    json={
                        "to": "all",
                        "visibility": "public",
                        "dry_run": True,
                        "skip_existing": True,
                        "delete_source": False,
                    },
                )
                self.assertEqual(migrated.status_code, 200, migrated.get_data(as_text=True))
                body = migrated.get_json()
                self.assertEqual(len(body["results"]), len(app.MIGRATION_TARGETS))
                self.assertEqual({item[0] for item in calls}, {item["id"] for item in app.MIGRATION_TARGETS})
                self.assertTrue(all(item["dry_run"] for item in body["results"]))


if __name__ == "__main__":
    unittest.main()
