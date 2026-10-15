from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, BinaryIO, Optional
from urllib.parse import quote

from py_storebridge.config import LogLevel, RetryConfig, SupabaseCredentials, Visibility
from py_storebridge.exceptions import FileNotFoundError, NetworkError, PermissionError, ProviderError
from py_storebridge.io import resolve_body
from py_storebridge.providers._deps import require
from py_storebridge.providers.base import BaseProvider, as_bytes_io
from py_storebridge.schemas import (
    CopyOptions,
    FileListEntry,
    FileMetadata,
    ListOptions,
    ListResult,
    SignedUrlOptions,
    UploadOptions,
    UploadResult,
)


class SupabaseProvider(BaseProvider):
    provider_name = "supabase"
    extra_name = "supabase"

    def __init__(
        self,
        credentials: SupabaseCredentials,
        *,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
        client: Any = None,
    ) -> None:
        super().__init__(retry=retry, log_level=log_level, default_visibility=default_visibility)
        self.base_url = credentials.url.rstrip("/")
        self.bucket = credentials.bucket
        self.cdn_url = credentials.cdn_url
        self.custom_domain = credentials.custom_domain
        httpx = require("httpx", self.extra_name)
        headers = {
            "Authorization": f"Bearer {credentials.service_role_key}",
            "apikey": credentials.service_role_key,
        }
        self.http = client or httpx.Client(
            base_url=f"{self.base_url}/storage/v1",
            headers=headers,
            timeout=60.0,
        )

    def _object_path(self, key: str) -> str:
        return f"/object/{self.bucket}/{quote(key, safe='/')}"

    def _upload(self, options: UploadOptions) -> UploadResult:
        body, size = resolve_body(options.file, options.path)
        headers = {"x-upsert": "true"}
        if options.content_type:
            headers["Content-Type"] = options.content_type
        if options.cache_control:
            headers["cache-control"] = options.cache_control
        try:
            response = self.http.post(self._object_path(options.key), content=body, headers=headers)
            _raise_http(response, self.provider_name, options.key)
        except ProviderError:
            raise
        except Exception as exc:
            raise NetworkError(str(exc), self.provider_name, exc) from exc
        return UploadResult(
            key=options.key,
            url=self._get_url(options.key),
            provider=self.provider_name,
            size=size,
            content_type=options.content_type,
            metadata=options.metadata,
        )

    def _delete(self, key: str) -> None:
        try:
            response = self.http.request(
                "DELETE",
                f"/object/{self.bucket}",
                json={"prefixes": [key]},
            )
            if response.status_code in {404, 400}:
                return
            _raise_http(response, self.provider_name, key)
        except ProviderError:
            raise
        except Exception as exc:
            raise NetworkError(str(exc), self.provider_name, exc) from exc

    def _exists(self, key: str) -> bool:
        try:
            self._get_metadata(key)
            return True
        except FileNotFoundError:
            return False

    def _get_metadata(self, key: str) -> FileMetadata:
        try:
            response = self.http.get(
                f"/object/info/authenticated/{self.bucket}/{quote(key, safe='/')}"
            )
            if response.status_code == 404:
                raise FileNotFoundError(key, self.provider_name)
            if response.status_code >= 400:
                # Fallback: authenticated download of a 1-byte range
                head = self.http.get(
                    f"/object/authenticated/{self.bucket}/{quote(key, safe='/')}",
                    headers={"Range": "bytes=0-0"},
                )
                if head.status_code in {404, 400}:
                    raise FileNotFoundError(key, self.provider_name)
                _raise_http(head, self.provider_name, key)
                size_header = head.headers.get("content-range") or head.headers.get("content-length") or "0"
                size = _parse_size(size_header)
                return FileMetadata(
                    key=key,
                    size=size,
                    content_type=head.headers.get("content-type") or "application/octet-stream",
                    last_modified=_parse_date(head.headers.get("last-modified")),
                    etag=(head.headers.get("etag") or "").strip('"') or None,
                )
            payload = response.json()
        except FileNotFoundError:
            raise
        except ProviderError:
            raise
        except Exception as exc:
            raise NetworkError(str(exc), self.provider_name, exc) from exc
        return FileMetadata(
            key=key,
            size=int(payload.get("metadata", {}).get("size") or payload.get("contentLength") or 0),
            content_type=payload.get("metadata", {}).get("mimetype")
            or payload.get("contentType")
            or "application/octet-stream",
            last_modified=_parse_date(payload.get("updated_at") or payload.get("created_at")),
            etag=payload.get("id"),
            metadata=payload.get("user_metadata") or None,
        )

    def _list(self, options: Optional[ListOptions] = None) -> ListResult:
        prefix = (options.prefix if options else None) or ""
        limit = (options.limit if options else None) or 100
        offset = 0
        if options and options.cursor:
            try:
                offset = int(options.cursor)
            except ValueError:
                offset = 0
        body = {
            "prefix": prefix,
            "limit": limit,
            "offset": offset,
            "sortBy": {"column": "name", "order": "asc"},
        }
        try:
            response = self.http.post(f"/object/list/{self.bucket}", json=body)
            _raise_http(response, self.provider_name)
            rows = response.json() or []
        except ProviderError:
            raise
        except Exception as exc:
            raise NetworkError(str(exc), self.provider_name, exc) from exc
        files = []
        prefixes = []
        for row in rows:
            name = row.get("name") or ""
            full = f"{prefix.rstrip('/')}/{name}".lstrip("/") if prefix else name
            if row.get("id") is None:
                prefixes.append(full if full.endswith("/") else f"{full}/")
                continue
            files.append(
                FileListEntry(
                    key=full,
                    size=int((row.get("metadata") or {}).get("size") or 0),
                    last_modified=_parse_date(row.get("updated_at") or row.get("created_at")),
                    etag=row.get("id"),
                )
            )
        next_cursor = str(offset + limit) if len(rows) >= limit else None
        return ListResult(files=files, next_cursor=next_cursor, prefixes=prefixes or None)

    def _get_url(self, key: str) -> str:
        encoded = quote(key, safe="/")
        if self.custom_domain:
            return f"{self.custom_domain.rstrip('/')}/{encoded}"
        if self.cdn_url:
            return f"{self.cdn_url.rstrip('/')}/{encoded}"
        return f"{self.base_url}/storage/v1/object/public/{self.bucket}/{encoded}"

    def _get_signed_url(self, key: str, options: Optional[SignedUrlOptions] = None) -> str:
        opts = options or SignedUrlOptions()
        payload: dict[str, Any] = {"expiresIn": opts.expires_in}
        try:
            response = self.http.post(
                f"/object/sign/{self.bucket}/{quote(key, safe='/')}",
                json=payload,
            )
            _raise_http(response, self.provider_name, key)
            data = response.json()
        except ProviderError:
            raise
        except Exception as exc:
            raise NetworkError(str(exc), self.provider_name, exc) from exc
        signed = data.get("signedURL") or data.get("signedUrl") or ""
        if signed.startswith("http"):
            return signed
        return f"{self.base_url}/storage/v1{signed}"

    def _copy(self, options: CopyOptions) -> UploadResult:
        payload = {
            "bucketId": self.bucket,
            "sourceKey": options.source_key,
            "destinationKey": options.destination_key,
        }
        try:
            response = self.http.post("/object/copy", json=payload)
            _raise_http(response, self.provider_name, options.source_key)
        except ProviderError:
            raise
        except Exception as exc:
            raise NetworkError(str(exc), self.provider_name, exc) from exc
        return UploadResult(
            key=options.destination_key,
            url=self._get_url(options.destination_key),
            provider=self.provider_name,
            content_type=options.content_type,
            metadata=options.metadata,
        )

    def _get_stream(self, key: str) -> BinaryIO:
        try:
            response = self.http.get(f"/object/authenticated/{self.bucket}/{quote(key, safe='/')}")
            _raise_http(response, self.provider_name, key)
        except ProviderError:
            raise
        except Exception as exc:
            raise NetworkError(str(exc), self.provider_name, exc) from exc
        return as_bytes_io(response.content)


def _parse_date(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if not value:
        return datetime.now(timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)


def _parse_size(header: str) -> int:
    if "/" in header:
        try:
            return int(header.split("/")[-1])
        except ValueError:
            return 0
    try:
        return int(header)
    except ValueError:
        return 0


def _raise_http(response: Any, provider: str, key: Optional[str] = None) -> None:
    status = getattr(response, "status_code", 500)
    if status < 400:
        return
    if status == 404:
        if key:
            raise FileNotFoundError(key, provider)
        raise ProviderError(response.text, provider, status_code=404)
    if status in {401, 403}:
        raise PermissionError(response.text, provider)
    if status in {408, 429, 500, 502, 503, 504}:
        raise NetworkError(response.text, provider)
    raise ProviderError(response.text, provider, status_code=status)
