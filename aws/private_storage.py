"""Direct private AWS S3 client for py_storebridge.

https://pypi.org/project/py-storebridge/1.0.0/#aws-s3
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
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION"):
        if not _env(name):
            gaps.append(name)
    if not (_env("AWS_PRIVATE_BUCKET") or _env("AWS_BUCKET")):
        gaps.append("AWS_PRIVATE_BUCKET or AWS_BUCKET")
    return gaps


def build_config() -> dict:
    """Private storage config. Visibility is fixed to private."""
    gaps = missing()
    if gaps:
        raise RuntimeError("Missing settings: " + ", ".join(gaps))

    credentials = {
        "access_key_id": _env("AWS_ACCESS_KEY_ID"),
        "secret_access_key": _env("AWS_SECRET_ACCESS_KEY"),
        "region": _env("AWS_REGION"),
        "bucket": _env("AWS_PRIVATE_BUCKET") or _env("AWS_BUCKET"),
    }
    if _env("AWS_SESSION_TOKEN"):
        credentials["session_token"] = _env("AWS_SESSION_TOKEN")
    if _env("AWS_ENDPOINT"):
        credentials["endpoint"] = _env("AWS_ENDPOINT")
    if _env("AWS_CDN_URL"):
        credentials["cdn_url"] = _env("AWS_CDN_URL")
    if _env("AWS_CUSTOM_DOMAIN"):
        credentials["custom_domain"] = _env("AWS_CUSTOM_DOMAIN")

    return {
        "provider": "s3",
        "credentials": credentials,
        "visibility": "private",
        "retry": {"max_retries": 3, "base_delay": 200, "max_delay": 10000},
        "log_level": "warn",
    }


@lru_cache(maxsize=1)
def client():
    return create_storage(build_config())
