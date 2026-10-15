"""Direct private Google Cloud Storage client for py_storebridge.

https://pypi.org/project/py-storebridge/1.0.0/#google-cloud-storage
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from py_storebridge import create_storage

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parent / ".env")
load_dotenv(HERE / ".env", override=True)


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def _key_file() -> str:
    raw = _env("GCP_KEY_FILENAME")
    if not raw:
        return ""
    path = Path(raw)
    if path.is_file():
        return str(path.resolve())
    for base in (HERE, HERE.parent):
        candidate = (base / raw).resolve()
        if candidate.is_file():
            return str(candidate)
    return ""


def missing() -> list[str]:
    """Names of settings this private client still needs. Values are never returned."""
    gaps = []
    if not _env("GCP_PROJECT_ID"):
        gaps.append("GCP_PROJECT_ID")
    if not (_env("GCP_PRIVATE_BUCKET") or _env("GCP_BUCKET")):
        gaps.append("GCP_PRIVATE_BUCKET or GCP_BUCKET")
    has_email_key = bool(_env("GCP_CLIENT_EMAIL") and _env("GCP_PRIVATE_KEY"))
    if _env("GCP_KEY_FILENAME") and not _key_file():
        gaps.append("GCP_KEY_FILENAME (file not found)")
    elif not _key_file() and not has_email_key:
        gaps.append("GCP_KEY_FILENAME or GCP_CLIENT_EMAIL + GCP_PRIVATE_KEY")
    return gaps


def build_config() -> dict:
    """Private storage config. Visibility is fixed to private."""
    gaps = missing()
    if gaps:
        raise RuntimeError("Missing settings: " + ", ".join(gaps))

    credentials = {
        "project_id": _env("GCP_PROJECT_ID"),
        "bucket": _env("GCP_PRIVATE_BUCKET") or _env("GCP_BUCKET"),
    }
    key_file = _key_file()
    if key_file:
        credentials["key_filename"] = key_file
    else:
        credentials["credentials"] = {
            "client_email": _env("GCP_CLIENT_EMAIL"),
            "private_key": _env("GCP_PRIVATE_KEY"),
        }
    if _env("GCP_CDN_URL"):
        credentials["cdn_url"] = _env("GCP_CDN_URL")
    if _env("GCP_CUSTOM_DOMAIN"):
        credentials["custom_domain"] = _env("GCP_CUSTOM_DOMAIN")

    return {
        "provider": "gcs",
        "credentials": credentials,
        "visibility": "private",
        "retry": {"max_retries": 3, "base_delay": 200, "max_delay": 10000},
        "log_level": "warn",
    }


@lru_cache(maxsize=1)
def client():
    return create_storage(build_config())
