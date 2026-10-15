"""Storage key validation and path helpers."""

from __future__ import annotations

from typing import Optional

from py_storebridge.exceptions import ValidationError

_MAX_KEY_LENGTH = 1024


def validate_key(key: str) -> str:
    if not isinstance(key, str) or not key.strip():
        raise ValidationError("Storage key must be a non-empty string", field="key")
    key = key.strip()
    if "\\" in key:
        raise ValidationError("Storage key must use '/' as the path separator", field="key")
    if key.startswith("/"):
        raise ValidationError("Storage key must not start with '/'", field="key")
    if key.endswith("/"):
        raise ValidationError("Storage key must not end with '/'", field="key")
    parts = key.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ValidationError("Storage key contains an empty or relative path segment", field="key")
    if len(key) > _MAX_KEY_LENGTH:
        raise ValidationError(f"Storage key exceeds {_MAX_KEY_LENGTH} characters", field="key")
    return key


def join_key(*parts: Optional[str]) -> str:
    chunks = []
    for part in parts:
        if not part:
            continue
        chunks.append(part.strip("/"))
    return "/".join(chunk for chunk in chunks if chunk)


def map_transfer_key(
    source_key: str,
    *,
    source_prefix: Optional[str] = None,
    destination_prefix: Optional[str] = None,
    destination_key: Optional[str] = None,
) -> str:
    if destination_key:
        return destination_key
    if source_prefix is not None and destination_prefix is not None:
        prefix = source_prefix if source_prefix.endswith("/") else source_prefix
        if source_prefix and source_key.startswith(source_prefix):
            relative = source_key[len(source_prefix) :].lstrip("/")
        elif source_prefix and source_key.startswith(source_prefix.rstrip("/") + "/"):
            relative = source_key[len(source_prefix.rstrip("/")) + 1 :]
        else:
            relative = source_key
        return join_key(destination_prefix, relative) if destination_prefix else relative
    if destination_prefix:
        return join_key(destination_prefix, source_key)
    return source_key
