# Migrations

py_storebridge 1.0.0 copies objects that already exist with `transfer(source, destination, ...)`. Upload a file in the source app first. Migration does not take a file from the browser. It lists the source prefix, reads each object with `get_buffer()`, and uploads it to the other provider.

Each folder has its own script. The functions are separate for public and private, and for each destination.

| Source folder | Script | Separate functions |
| --- | --- | --- |
| `aws` | `aws/migrate.py` | public to Azure, private to Azure, public to GCS, private to GCS |
| `azure` | `azure/migrate.py` | public to AWS, private to AWS, public to GCS, private to GCS |
| `gcp` | `gcp/migrate.py` | public to AWS, private to AWS, public to Azure, private to Azure |

Public migrations use the public client on both sides and the prefix `public/`. Private migrations use the private clients and the prefix `private/`. The call keeps metadata and visibility, uses 8 workers, and continues after an item error. That matches the transfer example in the library docs.

The same actions are on each `index.html` page. Dry run starts checked. "Delete source after copy" asks for confirmation because it deletes the source object after a successful copy.

## Commands

Run these from the source folder, after both sides have credentials in `.env`. Start with `--dry-run`. A dry run lists the plan and does not upload or delete.

AWS to the others:

```powershell
cd aws
python migrate.py --to azure --visibility public --dry-run
python migrate.py --to gcp --visibility private --dry-run
python migrate.py --to all --visibility both --skip-existing
```

Azure to the others:

```powershell
cd azure
python migrate.py --to aws --visibility public --dry-run
python migrate.py --to gcp --visibility private --dry-run
python migrate.py --to all --visibility both --skip-existing
```

GCS to the others:

```powershell
cd gcp
python migrate.py --to aws --visibility public --dry-run
python migrate.py --to azure --visibility private --dry-run
python migrate.py --to all --visibility both --skip-existing
```

Copy one object by its catalog key:

```powershell
python migrate.py --to azure --visibility public --source-key public/abc_notes.txt --dry-run
```

`--visibility` selects the client. With `--source-key`, that exact key is copied and the prefix is not used.

## Flags

| Flag | Effect |
| --- | --- |
| `--to azure\|gcp\|aws\|all` | Destination folder. Choices depend on the source script |
| `--visibility public\|private\|both` | Which client and prefix to use |
| `--source-key` | One object instead of the whole prefix |
| `--dry-run` | Plan only |
| `--skip-existing` | Leave destination keys that already exist |
| `--delete-source` | Delete the source object after a successful copy |

The page sends `skip_existing: true` when its checkbox is checked. The CLI copies again unless you pass `--skip-existing`.

## What you need on both sides

The destination app does not have to be running. The script builds that provider's client from the same `.env`. The destination bucket or container must already exist. The source credentials need list and read. The destination credentials need write.

A failed item is counted in `failed` and the rest continue. The JSON result includes `transferred`, `skipped`, `failed`, `planned`, `bytes_transferred`, and `items`.
