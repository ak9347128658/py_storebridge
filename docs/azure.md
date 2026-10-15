# Azure Blob Storage

Library provider id: `azure`. App folder: `azure`. Page: http://127.0.0.1:8002

The direct clients are `azure/public_storage.py` and `azure/private_storage.py`. Both call `create_storage()` with the shape from the [Azure section](https://pypi.org/project/py-storebridge/1.0.0/#azure-blob-storage). Public sets `"visibility": "public"`. Private sets `"visibility": "private"`.

py_storebridge 1.0.0 marks Azure ACL support as limited. These clients still pass visibility into `upload()`, and the page keeps public and private objects in different prefixes. A public browser URL works only when the container allows anonymous blob read. A private read uses `get_signed_url()`, which needs `AZURE_ACCOUNT_KEY`.

## Create these resources first

1. A storage account.
2. A blob container. Use one container for both clients, or two containers and set `AZURE_PUBLIC_CONTAINER` and `AZURE_PRIVATE_CONTAINER`.
3. The account access key. A connection string can upload and download. Signed private URLs still need the account key, so set `AZURE_ACCOUNT_KEY` when you want "Read again" to open a private object in the browser.
4. For public links, set the container's anonymous access level to Blob (anonymous read for blobs only). Leave it private if you only want signed URLs.

The app role needs blob read, write, delete, and list on that container.

## Settings

| Variable | Required | Purpose |
| --- | --- | --- |
| `AZURE_ACCOUNT_NAME` | yes | Storage account name |
| `AZURE_ACCOUNT_KEY` | yes, unless `AZURE_CONNECTION_STRING` is set | Account key. Required for signed URLs |
| `AZURE_CONNECTION_STRING` | no | Alternative client login |
| `AZURE_CONTAINER` | yes, unless both specific containers are set | Shared container |
| `AZURE_PUBLIC_CONTAINER` | no | Container used only by the public client |
| `AZURE_PRIVATE_CONTAINER` | no | Container used only by the private client |
| `AZURE_CDN_URL` | no | CDN base URL |
| `AZURE_CUSTOM_DOMAIN` | no | Custom file domain |

## Run

```powershell
.\.venv\Scripts\Activate.ps1
cd azure
python app.py
```

Open http://127.0.0.1:8002

- Public upload stores `public/<id>_<filename>`.
- Private upload stores `private/<id>_<filename>`.
- The catalog file is `azure/data/uploads.json`.

Migrations from this account live in `azure/migrate.py`. See [migrations.md](migrations.md).
