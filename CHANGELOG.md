# Changelog

## 1.1.0 — 2026-10-17

### On-premises storage

- New `onprem` provider for private and public files on your own machines
- `engine='auto'` stores on the local disk when no storage service is configured. No extra package and no download
- Point `endpoint` at an existing S3-compatible service: MinIO, Ceph RGW, Garage, or SeaweedFS (`pip install py_storebridge[onprem]`)
- `install=true` bootstraps MinIO on this machine when the endpoint is down. Auto mode uses a MinIO binary on PATH, then Docker. Set `allow_download=true` to fetch the official MinIO binary
- `engine='filesystem'` never installs a service. `serve=true` exposes public files and HMAC signed URLs from the Python process
- Filesystem keys are encoded so Windows reserved names and path segments cannot escape the bucket directory

## 1.0.0 — 2026-03-25

First public release of **py_storebridge**, the Python port of [storebridge](https://www.npmjs.com/package/storebridge).

- Unified API across AWS S3, Cloudflare R2, Google Cloud Storage, Azure Blob, DigitalOcean Spaces, Backblaze B2, MinIO, Supabase, Firebase, and Appwrite
- Pydantic v2 validation for configuration and every public operation
- Retry with exponential backoff and full jitter
- Upload middleware and lifecycle hooks
- Multipart upload (native on S3-compatible backends, fallback elsewhere)
- Signed / presigned URLs
- Streaming upload and download
- **Cross-provider transfer** of files, folders (prefixes), and entire buckets
- Optional extras so provider SDKs are installed only when needed
