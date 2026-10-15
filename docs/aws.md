# AWS S3

Library provider id: `s3`. App folder: `aws`. Page: http://127.0.0.1:8001

The direct clients are `aws/public_storage.py` and `aws/private_storage.py`. Both call `create_storage()` with the shape from the [AWS S3 section](https://pypi.org/project/py-storebridge/1.0.0/#aws-s3). Public sets `"visibility": "public"`. Private sets `"visibility": "private"`.

## Create these resources first

1. An S3 bucket in the region you will put in `AWS_REGION`.
2. An IAM user access key that can `s3:PutObject`, `s3:GetObject`, `s3:DeleteObject`, `s3:ListBucket`, and `s3:GetObjectAttributes` on that bucket.
3. For a public upload, object ACLs must be allowed. In the bucket, set Object Ownership to "Bucket owner preferred" and allow public ACLs if you want `public-read`. If ACLs are blocked, py_storebridge retries the upload without an ACL, and the object stays private even though this app asked for public.

You can use one bucket for both clients, or set `AWS_PUBLIC_BUCKET` and `AWS_PRIVATE_BUCKET` separately. When a specific bucket variable is empty, the client uses `AWS_BUCKET`.

## Settings

Put these in the root `.env`, or in `aws/.env`:

| Variable | Required | Purpose |
| --- | --- | --- |
| `AWS_ACCESS_KEY_ID` | yes | Access key |
| `AWS_SECRET_ACCESS_KEY` | yes | Secret key |
| `AWS_REGION` | yes | Bucket region, for example `us-east-1` |
| `AWS_BUCKET` | yes, unless both specific buckets are set | Shared bucket |
| `AWS_PUBLIC_BUCKET` | no | Bucket used only by the public client |
| `AWS_PRIVATE_BUCKET` | no | Bucket used only by the private client |
| `AWS_SESSION_TOKEN` | no | Temporary credentials |
| `AWS_ENDPOINT` | no | S3-compatible endpoint |
| `AWS_CDN_URL` | no | CDN base URL |
| `AWS_CUSTOM_DOMAIN` | no | Custom file domain |

## Run

```powershell
.\.venv\Scripts\Activate.ps1
cd aws
python app.py
```

Open http://127.0.0.1:8001

- Public upload stores `public/<id>_<filename>` and reads it back with `get_buffer()`.
- Private upload stores `private/<id>_<filename>`. "Read again" asks for a signed GET URL.
- The catalog file is `aws/data/uploads.json`.

Migrations from this bucket live in `aws/migrate.py`. See [migrations.md](migrations.md).
