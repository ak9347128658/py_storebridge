"""Migrate existing Azure Blob objects to AWS S3 and to GCS.

Each function is a separate migration. They call ``py_storebridge.transfer``.

Examples (from this folder):

    python migrate.py --to aws --visibility public --dry-run
    python migrate.py --to gcp --visibility private --skip-existing
    python migrate.py --to all --visibility both --dry-run
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from py_storebridge import transfer

from private_storage import client as private_client
from public_storage import client as public_client

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")
load_dotenv(HERE / ".env", override=True)


def _load(folder: str, filename: str):
    path = HERE.parent / folder / filename
    module_name = f"demo_{folder}_{filename[:-3]}"
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _transfer(source, destination, visibility: str, *, source_key, dry_run, delete_source, skip_existing):
    shared = dict(
        concurrency=8,
        preserve_metadata=True,
        preserve_visibility=True,
        dry_run=dry_run,
        delete_source=delete_source,
        skip_existing=skip_existing,
        overwrite=True,
        continue_on_error=True,
    )
    if source_key:
        return transfer(
            source,
            destination,
            source_key=source_key,
            destination_key=source_key,
            **shared,
        )
    prefix = f"{visibility}/"
    return transfer(
        source,
        destination,
        prefix=prefix,
        destination_prefix=prefix,
        **shared,
    )


def migrate_public_to_aws(*, source_key=None, dry_run=False, delete_source=False, skip_existing=False):
    """Copy public Azure objects to the public AWS client."""
    destination = _load("aws", "public_storage.py").client()
    return _transfer(
        public_client(),
        destination,
        "public",
        source_key=source_key,
        dry_run=dry_run,
        delete_source=delete_source,
        skip_existing=skip_existing,
    )


def migrate_private_to_aws(*, source_key=None, dry_run=False, delete_source=False, skip_existing=False):
    """Copy private Azure objects to the private AWS client."""
    destination = _load("aws", "private_storage.py").client()
    return _transfer(
        private_client(),
        destination,
        "private",
        source_key=source_key,
        dry_run=dry_run,
        delete_source=delete_source,
        skip_existing=skip_existing,
    )


def migrate_public_to_gcp(*, source_key=None, dry_run=False, delete_source=False, skip_existing=False):
    """Copy public Azure objects to the public GCS client."""
    destination = _load("gcp", "public_storage.py").client()
    return _transfer(
        public_client(),
        destination,
        "public",
        source_key=source_key,
        dry_run=dry_run,
        delete_source=delete_source,
        skip_existing=skip_existing,
    )


def migrate_private_to_gcp(*, source_key=None, dry_run=False, delete_source=False, skip_existing=False):
    """Copy private Azure objects to the private GCS client."""
    destination = _load("gcp", "private_storage.py").client()
    return _transfer(
        private_client(),
        destination,
        "private",
        source_key=source_key,
        dry_run=dry_run,
        delete_source=delete_source,
        skip_existing=skip_existing,
    )


_ROUTES = {
    ("aws", "public"): migrate_public_to_aws,
    ("aws", "private"): migrate_private_to_aws,
    ("gcp", "public"): migrate_public_to_gcp,
    ("gcp", "private"): migrate_private_to_gcp,
}


def run(target: str, visibility: str, **kwargs):
    fn = _ROUTES.get((target, visibility))
    if fn is None:
        raise RuntimeError(f"No Azure migration for target={target!r} visibility={visibility!r}")
    return fn(**kwargs)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Migrate Azure objects with py_storebridge.transfer")
    parser.add_argument("--to", required=True, choices=["aws", "gcp", "all"])
    parser.add_argument("--visibility", default="public", choices=["public", "private", "both"])
    parser.add_argument("--source-key", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--delete-source", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    args = parser.parse_args(argv)

    targets = ["aws", "gcp"] if args.to == "all" else [args.to]
    visibilities = ["public", "private"] if args.visibility == "both" else [args.visibility]
    results = []
    try:
        for target in targets:
            for visibility in visibilities:
                result = run(
                    target,
                    visibility,
                    source_key=args.source_key,
                    dry_run=args.dry_run,
                    delete_source=args.delete_source,
                    skip_existing=args.skip_existing,
                )
                payload = result.model_dump(mode="json")
                payload["target"] = target
                payload["visibility"] = visibility
                results.append(payload)
    except Exception as exc:  # noqa: BLE001 — CLI should print the library error and stop
        print(str(exc), file=sys.stderr)
        return 1
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
