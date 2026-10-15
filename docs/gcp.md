# Google Cloud Storage

Library provider id: `gcs`. App folder: `gcp`. Page: http://127.0.0.1:8003

The direct clients are `gcp/public_storage.py` and `gcp/private_storage.py`. Both call `create_storage()` with the shape from the [GCS section](https://pypi.org/project/py-storebridge/1.0.0/#google-cloud-storage). Public sets `"visibility": "public"`. Private sets `"visibility": "private"`.

## Create these resources first

1. A Google Cloud project.
2. A bucket. Use one bucket for both clients, or set `GCP_PUBLIC_BUCKET` and `GCP_PRIVATE_BUCKET`.
3. A service account with Storage Object Admin on that bucket (`storage.objects.create`, `get`, `list`, `delete`).
4. A JSON key for that service account.

Public upload calls `blob.make_public()`. That works when the bucket allows object ACLs. If uniform bucket-level access is on, GCS rejects the ACL change; the library catches that error and the object stays private to the bucket IAM policy. For a real public object with uniform access, grant `allUsers` the Storage Object Viewer role, or use signed URLs from the private flow instead.

## Settings

Choose one login. A key file wins when `GCP_KEY_FILENAME` is set and the file exists.

| Variable | Required | Purpose |
| --- | --- | --- |
| `GCP_PROJECT_ID` | yes | Project id |
| `GCP_BUCKET` | yes, unless both specific buckets are set | Shared bucket |
| `GCP_PUBLIC_BUCKET` | no | Bucket used only by the public client |
| `GCP_PRIVATE_BUCKET` | no | Bucket used only by the private client |
| `GCP_KEY_FILENAME` | one of these logins | Path to the service-account JSON file. Relative paths are checked in `gcp/` and the repo root |
| `GCP_CLIENT_EMAIL` | the other login | Service account email |
| `GCP_PRIVATE_KEY` | with the email | PEM private key. In `.env`, wrap it in double quotes so `\n` stays intact |
| `GCP_CDN_URL` | no | CDN base URL |
| `GCP_CUSTOM_DOMAIN` | no | Custom file domain |

Email login matches the docs:

```python
"credentials": {
    "client_email": "...",
    "private_key": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n",
}
```

## Run

```powershell
.\.venv\Scripts\Activate.ps1
cd gcp
python app.py
```

Open http://127.0.0.1:8003

- Public upload stores `public/<id>_<filename>`.
- Private upload stores `private/<id>_<filename>`. "Read again" uses a signed GET URL.
- The catalog file is `gcp/data/uploads.json`.

Migrations from this bucket live in `gcp/migrate.py`. See [migrations.md](migrations.md).
