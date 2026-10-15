# py_storebridge 1.0.0 test apps

Three local apps exercise [py-storebridge 1.0.0](https://pypi.org/project/py-storebridge/1.0.0/): AWS S3, Azure Blob Storage, and Google Cloud Storage. Each folder has its own public client, private client, upload page, and migration script.

| Folder | Library provider | Page | What to read |
| --- | --- | --- | --- |
| `aws` | `s3` | http://127.0.0.1:8001 | [aws.md](aws.md) |
| `azure` | `azure` | http://127.0.0.1:8002 | [azure.md](azure.md) |
| `gcp` | `gcs` | http://127.0.0.1:8003 | [gcp.md](gcp.md) |

Cross-provider copy is documented in [migrations.md](migrations.md). The library call is `py_storebridge.transfer`.

## Install

From the repository root, with the existing virtual environment:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

`requirements.txt` installs `py-storebridge[s3,gcs,azure]==1.0.0`, Flask, and python-dotenv. Provider SDKs are the `s3`, `gcs`, and `azure` extras. The core package does not install them by itself.

## Credentials

```powershell
Copy-Item .env.example .env
```

Fill in `.env`. Do not commit it. A file named `.env` inside `aws`, `azure`, or `gcp` overrides the root file for that app only.

The pages report missing variable names. They do not print secret values.

## What each page does

1. You choose the public form or the private form and upload a file.
2. The matching client calls `upload()` with `visibility` set to `public` or `private`.
3. The server calls `get_buffer()` and checks that those bytes match the upload.
4. The server writes the record, including text or base64 content up to 1 MB, to `data/uploads.json`.
5. The page lists that catalog and can read the object again.

Public objects use keys that start with `public/`. Private objects use keys that start with `private/`.

## Run one provider

Open a terminal per provider. The working directory matters because each app imports the clients in its own folder.

```powershell
.\.venv\Scripts\Activate.ps1
cd aws
python app.py
```

Repeat in other terminals with `cd azure` and `cd gcp`.

## Checks without cloud credentials

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

That test validates the public and private configs, runs `transfer()` between in-memory providers, and posts a file through each Flask app. It does not call AWS, Azure, or Google.
