# Changelog

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
