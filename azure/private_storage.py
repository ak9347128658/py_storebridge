"""Direct private Azure Blob client for py_storebridge.

https://pypi.org/project/py-storebridge/1.0.0/#azure-blob-storage
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


def missing() -> list[str]:
    """Names of settings this private client still needs. Values are never returned."""
    gaps = []
    if not _env("AZURE_ACCOUNT_NAME"):
        gaps.append("AZURE_ACCOUNT_NAME")
    if not (_env("AZURE_PRIVATE_CONTAINER") or _env("AZURE_CONTAINER")):
        gaps.append("AZURE_PRIVATE_CONTAINER or AZURE_CONTAINER")
    if not (_env("AZURE_ACCOUNT_KEY") or _env("AZURE_CONNECTION_STRING")):
        gaps.append("AZURE_ACCOUNT_KEY or AZURE_CONNECTION_STRING")
    return gaps


def build_config() -> dict:
    """Private storage config. Visibility is fixed to private."""
    gaps = missing()
    if gaps:
        raise RuntimeError("Missing settings: " + ", ".join(gaps))

    credentials = {
        "account_name": _env("AZURE_ACCOUNT_NAME"),
        "container_name": _env("AZURE_PRIVATE_CONTAINER") or _env("AZURE_CONTAINER"),
    }
    if _env("AZURE_ACCOUNT_KEY"):
        credentials["account_key"] = _env("AZURE_ACCOUNT_KEY")
    if _env("AZURE_CONNECTION_STRING"):
        credentials["connection_string"] = _env("AZURE_CONNECTION_STRING")
    if _env("AZURE_CDN_URL"):
        credentials["cdn_url"] = _env("AZURE_CDN_URL")
    if _env("AZURE_CUSTOM_DOMAIN"):
        credentials["custom_domain"] = _env("AZURE_CUSTOM_DOMAIN")

    return {
        "provider": "azure",
        "credentials": credentials,
        "visibility": "private",
        "retry": {"max_retries": 3, "base_delay": 200, "max_delay": 10000},
        "log_level": "warn",
    }


@lru_cache(maxsize=1)
def client():
    return create_storage(build_config())
