"""HMAC signed URLs for the on-premises filesystem engine.

The signature covers the method, bucket, key, expiry, and content type.
It does not cover the host, so a reverse proxy can change the public origin
without invalidating tokens. Comparison is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Mapping, Optional
from urllib.parse import urlencode

EXPIRES_PARAM = "sb-expires"
SIGNATURE_PARAM = "sb-signature"
CONTENT_TYPE_PARAM = "sb-content-type"


def canonical_string(
    method: str,
    bucket: str,
    key: str,
    expires: str,
    content_type: str = "",
) -> bytes:
    return f"{method.upper()}\n{bucket}\n{key}\n{expires}\n{content_type}".encode("utf-8")


def sign(
    secret: str,
    method: str,
    bucket: str,
    key: str,
    expires: int,
    content_type: str = "",
) -> str:
    payload = canonical_string(method, bucket, key, str(expires), content_type or "")
    return hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def signed_query(
    secret: str,
    method: str,
    bucket: str,
    key: str,
    expires: int,
    content_type: str = "",
) -> str:
    params = {
        EXPIRES_PARAM: str(expires),
        SIGNATURE_PARAM: sign(secret, method, bucket, key, expires, content_type),
    }
    if content_type:
        params[CONTENT_TYPE_PARAM] = content_type
    return urlencode(params)


def first_query_value(query: Mapping[str, object], name: str) -> str:
    value = query.get(name, "")
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value or "")


def verify(
    secret: str,
    method: str,
    bucket: str,
    key: str,
    query: Mapping[str, object],
    *,
    now: float,
    header_content_type: str = "",
) -> Optional[str]:
    """Return ``None`` when the token is valid, otherwise a short reason."""
    expires_raw = first_query_value(query, EXPIRES_PARAM)
    provided = first_query_value(query, SIGNATURE_PARAM)
    content_type = first_query_value(query, CONTENT_TYPE_PARAM)
    if not expires_raw or not provided:
        return "missing signature"
    if not expires_raw.isdigit():
        return "invalid expiry"
    expires_at = int(expires_raw)
    if now >= expires_at:
        return "expired"
    if content_type and header_content_type != content_type:
        return "content type mismatch"
    expected = sign(secret, method, bucket, key, expires_at, content_type)
    if len(provided) != len(expected):
        return "invalid signature"
    if not hmac.compare_digest(provided, expected):
        return "invalid signature"
    return None
