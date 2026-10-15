# py_storebridge

**A unified, production-ready cloud storage SDK for Python.**

Switch storage providers by changing a single config line — no code changes required.

Python port of [storebridge](https://www.npmjs.com/package/storebridge) (Node.js / TypeScript), with Pydantic validation and **cross-provider transfer** of files, folders, and entire buckets.

---

## Features

- **10 storage providers** — one consistent API
- **Pydantic v2** — every config object and public operation is validated
- **Retry system** — exponential backoff with full jitter
- **Middleware** — composable upload pipeline (metadata injection, key prefixes, …)
- **Hooks** — lifecycle events (before/after upload, delete, copy, move, transfer)
- **Multipart upload** — high-level helper + low-level lifecycle API
- **Signed / presigned URLs** — GET, PUT, DELETE
- **Streaming** — upload from paths / file objects, download as streams or bytes
- **MIME auto-detection** — from file extension
- **CDN / custom domain** support
- **Lazy provider loading** — only the SDK you use is imported
- **Cross-provider transfer** — move existing files, folders, or a whole bucket from one provider to another

---

## Provider support

| Provider | Extra | Install |
| --- | --- | --- |
| AWS S3 | `s3` | `pip install py_storebridge[s3]` |
| Cloudflare R2 | `r2` | `pip install py_storebridge[r2]` |
| Google Cloud Storage | `gcs` | `pip install py_storebridge[gcs]` |
| Azure Blob Storage | `azure` | `pip install py_storebridge[azure]` |
| DigitalOcean Spaces | `spaces` | `pip install py_storebridge[spaces]` |
| Backblaze B2 | `b2` | `pip install py_storebridge[b2]` |
| MinIO | `minio` | `pip install py_storebridge[minio]` |
| Supabase Storage | `supabase` | `pip install py_storebridge[supabase]` |
| Firebase Storage | `firebase` | `pip install py_storebridge[firebase]` |
| Appwrite Storage | `appwrite` | `pip install py_storebridge[appwrite]` |

Provider SDKs are optional extras. The core package depends only on **Pydantic**.

```bash
pip install py_storebridge
pip install py_storebridge[s3]
pip install py_storebridge[all]
```

---

## Feature matrix

| Feature | S3 | R2 | GCS | Azure | Spaces | B2 | MinIO | Supabase | Firebase | Appwrite |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Upload | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| Delete | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| Exists | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| Metadata | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| List | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| Signed URLs | yes | yes | yes | yes | yes | yes | yes | yes | yes | limited |
| Copy / move | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| Stream upload | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| Multipart | native | native | fallback | native blocks | native | native | native | fallback | fallback | fallback |
| ACL / visibility | yes | limited | yes | limited | yes | limited | limited | yes | yes | yes |
| CDN / custom domain | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes |
| **Transfer to another provider** | yes | yes | yes | yes | yes | yes | yes | yes | yes | yes |

---

## Quick start

```python
from py_storebridge import create_storage

storage = create_storage({
    "provider": "s3",
    "credentials": {
        "access_key_id": "AKI…",
        "secret_access_key": "…",
        "region": "us-east-1",
        "bucket": "my-bucket",
    },
    "visibility": "private",
})

result = storage.upload(key="users/profile.png", file=image_bytes, content_type="image/png")
print(result.url)

url = storage.get_signed_url("users/profile.png", expires_in=3600)
exists = storage.exists("users/profile.png")
storage.delete(key="users/profile.png")
```

CamelCase credential keys from the Node.js SDK (`accessKeyId`, `secretAccessKey`, …) are accepted as well.

---

## Configuration

All providers share the same top-level shape. Credentials differ per provider. Pydantic validates the payload before any SDK is loaded.

```python
from py_storebridge import create_storage

storage = create_storage(
    provider="s3",
    credentials={
        "access_key_id": "…",
        "secret_access_key": "…",
        "region": "us-east-1",
        "bucket": "my-bucket",
    },
    visibility="private",          # default visibility
    retry={"max_retries": 3, "base_delay": 200, "max_delay": 10000},
    log_level="warn",              # debug | info | warn | error | silent
)
```

Retry delays are milliseconds, matching the Node.js package.

---

## API

### upload

```python
result = storage.upload(
    key="images/photo.jpg",
    file=buffer,                    # bytes | str | pathlib.Path | BinaryIO
    content_type="image/jpeg",      # optional — detected from the key
    visibility="public",
    metadata={"author": "alice"},
    cache_control="max-age=31536000",
    on_progress=lambda uploaded, total: print(uploaded, total),
)
# result.key, result.url, result.etag, result.size, result.provider

storage.upload_file("local/photo.jpg", key="images/photo.jpg")
```

### delete / exists / metadata / list

```python
storage.delete(key="images/photo.jpg")
storage.exists("images/photo.jpg")
meta = storage.get_metadata("images/photo.jpg")

page = storage.list(prefix="images/", limit=50, cursor=None, delimiter="/")
for entry in storage.iter_files(prefix="images/"):
    print(entry.key, entry.size)

storage.delete_prefix("tmp/")
```

### URLs

```python
storage.get_url("public/logo.png")
storage.get_signed_url("private/report.pdf", expires_in=900)
storage.get_signed_url("uploads/raw.csv", method="PUT", expires_in=300, content_type="text/csv")
```

### copy / move / rename

These operate **inside one provider**.

```python
storage.copy(source_key="original.png", destination_key="copies/backup.png")
storage.move(source_key="temp/upload.png", destination_key="final/upload.png")
storage.rename("old-name.png", "new-name.png")
```

### streams and buffers

```python
stream = storage.get_stream("videos/intro.mp4")
data = storage.get_buffer("config/settings.json")
storage.download_file("config/settings.json", "settings.json")
```

### multipart

```python
result = storage.upload_multipart({"key": "large/dataset.parquet", "file": payload}, chunk_size=10 * 1024 * 1024)

session = storage.create_multipart_upload("huge.bin", "application/octet-stream")
parts = [
    storage.upload_part(upload_id=session.upload_id, key=session.key, part_number=i + 1, body=chunk)
    for i, chunk in enumerate(chunks)
]
storage.complete_multipart_upload(session, parts)
# storage.abort_multipart_upload(session)
```

---

## Transfer between providers

This is the capability the Node.js `storebridge` package does not have. `transfer()` copies (or moves) **existing** objects from one provider to another: a single file, a folder prefix, an explicit key list, or an entire bucket.

```python
from py_storebridge import create_storage, transfer

s3 = create_storage({"provider": "s3", "credentials": {…}})
gcs = create_storage({"provider": "gcs", "credentials": {…}})

# One file
transfer(s3, gcs, source_key="users/profile.png", destination_key="migrated/profile.png")

# A folder (every key under the prefix)
transfer(
    s3,
    gcs,
    prefix="images/",
    destination_prefix="backup/images/",
    concurrency=8,
    preserve_metadata=True,
    preserve_visibility=True,
)

# Entire bucket
result = s3.transfer_to(gcs)

# Move (copy then delete from the source)
transfer(s3, gcs, prefix="uploads/", delete_source=True)

# Dry run
plan = transfer(s3, gcs, prefix="images/", dry_run=True)
print(plan.planned, plan.items)

# Skip objects that already exist on the destination
transfer(s3, gcs, prefix="images/", skip_existing=True, continue_on_error=True)
```

`TransferResult` includes `transferred`, `skipped`, `failed`, `bytes_transferred`, and per-item status. Set `continue_on_error=False` to stop on the first failure (`TransferError` then carries the partial result).

---

## Middleware

Middleware wraps every `upload()` call (onion model).

```python
def inject_metadata(ctx, nxt):
    ctx.options.metadata = {
        **(ctx.options.metadata or {}),
        "uploaded_by": "user-123",
    }
    return nxt()

def force_prefix(ctx, nxt):
    if not ctx.options.key.startswith("uploads/"):
        ctx.options.key = f"uploads/{ctx.options.key}"
    return nxt()

storage.use(inject_metadata)
storage.use(force_prefix)
```

---

## Hooks

```python
storage.on("after:upload", lambda result: print(result.key, result.provider))
storage.on("error", lambda payload: log(payload["operation"], payload["error"]))
storage.on("after:transfer", lambda result: print(result.transferred, result.failed))

storage.off("after:upload", handler)
```

Events: `before:upload`, `after:upload`, `before:delete`, `after:delete`, `before:copy`, `after:copy`, `before:move`, `after:move`, `before:list`, `after:list`, `before:getSignedUrl`, `after:getSignedUrl`, `before:transfer`, `after:transfer`, `error`.

---

## Errors

All errors extend `StoreBridgeError` (`StorixError` is an alias).

```python
from py_storebridge import (
    StoreBridgeError,
    FileNotFoundError,
    PermissionError,
    MaxRetriesExceededError,
    ValidationError,
    MissingDependencyError,
    TransferError,
)

try:
    storage.get_metadata("missing.png")
except FileNotFoundError as err:
    print(err.key, err.status_code, err.provider)
except MaxRetriesExceededError as err:
    print(err.attempts)
except StoreBridgeError as err:
    print(err.code)
```

Install the matching extra if you see `MissingDependencyError`:

```text
pip install py_storebridge[s3]
```

---

## Provider examples

### AWS S3

```python
storage = create_storage({
    "provider": "s3",
    "credentials": {
        "access_key_id": "…",
        "secret_access_key": "…",
        "region": "us-east-1",
        "bucket": "my-bucket",
        "cdn_url": "https://cdn.example.com",
        "custom_domain": "https://files.example.com",
    },
    "visibility": "public",
})
```

### Cloudflare R2

```python
storage = create_storage({
    "provider": "r2",
    "credentials": {
        "account_id": "…",
        "access_key_id": "…",
        "secret_access_key": "…",
        "bucket": "my-bucket",
        "cdn_url": "https://pub-xxx.r2.dev",
    },
})
```

### Google Cloud Storage

```python
storage = create_storage({
    "provider": "gcs",
    "credentials": {
        "project_id": "my-gcp-project",
        "bucket": "my-bucket",
        "credentials": {
            "client_email": "…",
            "private_key": "-----BEGIN PRIVATE KEY-----\\n…\\n-----END PRIVATE KEY-----\\n",
        },
    },
})
```

### Azure Blob Storage

```python
storage = create_storage({
    "provider": "azure",
    "credentials": {
        "account_name": "…",
        "account_key": "…",
        "container_name": "uploads",
    },
})
```

### DigitalOcean Spaces

```python
storage = create_storage({
    "provider": "spaces",
    "credentials": {
        "access_key_id": "…",
        "secret_access_key": "…",
        "region": "nyc3",
        "bucket": "my-space",
        "cdn_url": "https://my-space.nyc3.cdn.digitaloceanspaces.com",
    },
})
```

### Backblaze B2

```python
storage = create_storage({
    "provider": "b2",
    "credentials": {
        "application_key_id": "…",
        "application_key": "…",
        "bucket": "my-bucket",
        "region": "us-west-004",
    },
})
```

### MinIO

```python
storage = create_storage({
    "provider": "minio",
    "credentials": {
        "end_point": "minio.example.com",
        "port": 9000,
        "use_ssl": True,
        "access_key": "…",
        "secret_key": "…",
        "bucket": "my-bucket",
    },
})
```

### Supabase Storage

```python
storage = create_storage({
    "provider": "supabase",
    "credentials": {
        "url": "https://xyz.supabase.co",
        "service_role_key": "…",
        "bucket": "avatars",
    },
    "visibility": "public",
})
```

### Firebase Storage

```python
storage = create_storage({
    "provider": "firebase",
    "credentials": {
        "project_id": "my-project",
        "bucket": "my-project.appspot.com",
        "service_account": {…},  # full service-account JSON
    },
})
```

### Appwrite Storage

Appwrite file IDs cannot contain `/`. py_storebridge stores a stable SHA-256 of the key as the file ID and keeps the original key as the file name, so `users/profile.png` still works.

```python
storage = create_storage({
    "provider": "appwrite",
    "credentials": {
        "endpoint": "https://cloud.appwrite.io/v1",
        "project_id": "…",
        "api_key": "…",
        "bucket_id": "…",
    },
})
```

---

## Custom provider

```python
from py_storebridge import BaseProvider, create_storage_from_provider

class MyProvider(BaseProvider):
    provider_name = "my-provider"

    def _upload(self, options):
        ...

storage = create_storage_from_provider(MyProvider())
```

---

## Development

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -e ".[dev]"
pytest
```

Publishing to PyPI is documented in [docs/HOW_TO_DEPLOY.md](docs/HOW_TO_DEPLOY.md).

---

## License

MIT © [Asif Khan](https://github.com/ak9347128658)
