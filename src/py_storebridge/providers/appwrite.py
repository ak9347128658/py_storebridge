from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, BinaryIO, Optional
from urllib.parse import quote

from py_storebridge.config import AppwriteCredentials, LogLevel, RetryConfig, Visibility
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


def file_id_for_key(key: str) -> str:
    """Appwrite file IDs are 36 chars of [a-zA-Z0-9._-]. Hash the storage key."""
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
    return digest


class AppwriteProvider(BaseProvider):
    provider_name = "appwrite"
    extra_name = "appwrite"

    def __init__(
        self,
        credentials: AppwriteCredentials,
        *,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
        client: Any = None,
    ) -> None:
        super().__init__(retry=retry, log_level=log_level, default_visibility=default_visibility)
        self.endpoint = credentials.endpoint.rstrip("/")
        self.project_id = credentials.project_id
        self.bucket_id = credentials.bucket_id
        self.cdn_url = credentials.cdn_url
        self.custom_domain = credentials.custom_domain
        httpx = require("httpx", self.extra_name)
        headers = {
            "X-Appwrite-Project": credentials.project_id,
            "X-Appwrite-Key": credentials.api_key,
        }
        self.http = client or httpx.Client(base_url=self.endpoint, headers=headers, timeout=60.0)

    def _file_url(self, file_id: str, suffix: str = "") -> str:
        base = f"/storage/buckets/{self.bucket_id}/files/{file_id}"
        return f"{base}{suffix}"

    def _upload(self, options: UploadOptions) -> UploadResult:
        body, size = resolve_body(options.file, options.path)
        data = body if isinstance(body, bytes) else body.read()
        file_id = file_id_for_key(options.key)
        files = {"file": (options.key, data, options.content_type or "application/octet-stream")}
        form = {"fileId": file_id, "name": options.key}
        try:
            existing = self.http.get(self._file_url(file_id))
            if existing.status_code < 400:
                delete = self.http.delete(self._file_url(file_id))
                if delete.status_code >= 400 and delete.status_code != 404:
                    _raise_http(delete, self.provider_name, options.key)
            response = self.http.post(
                f"/storage/buckets/{self.bucket_id}/files",
                data=form,
                files=files,
            )
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
        file_id = file_id_for_key(key)
        try:
            response = self.http.delete(self._file_url(file_id))
            if response.status_code in {404}:
                return
            _raise_http(response, self.provider_name, key)
        except ProviderError:
            raise
        except Exception as exc:
            raise NetworkError(str(exc), self.provider_name, exc) from exc

    def _exists(self, key: str) -> bool:
        file_id = file_id_for_key(key)
        try:
            response = self.http.get(self._file_url(file_id))
            if response.status_code == 404:
                return False
            _raise_http(response, self.provider_name, key)
            return True
        except FileNotFoundError:
            return False
        except ProviderError:
            raise
        except Exception as exc:
            raise NetworkError(str(exc), self.provider_name, exc) from exc

    def _get_metadata(self, key: str) -> FileMetadata:
        file_id = file_id_for_key(key)
        try:
            response = self.http.get(self._file_url(file_id))
            _raise_http(response, self.provider_name, key)
            payload = response.json()
        except ProviderError:
            raise
        except Exception as exc:
            raise NetworkError(str(exc), self.provider_name, exc) from exc
        name = payload.get("name") or key
        return FileMetadata(
            key=name,
            size=int(payload.get("sizeOriginal") or 0),
            content_type=payload.get("mimeType") or "application/octet-stream",
            last_modified=_parse_date(payload.get("$updatedAt") or payload.get("$createdAt")),
            etag=payload.get("$id"),
            metadata={"original_key": name},
        )

    def _list(self, options: Optional[ListOptions] = None) -> ListResult:
        limit = (options.limit if options else None) or 100
        cursor = options.cursor if options else None
        prefix = options.prefix if options else None
        query = [f"limit({limit})"]
        if cursor:
            query.append(f'cursorAfter("{cursor}")')
        try:
            response = self.http.get(
                f"/storage/buckets/{self.bucket_id}/files",
                params=[("queries[]", item) for item in query],
            )
            _raise_http(response, self.provider_name)
            payload = response.json()
        except ProviderError:
            raise
        except Exception as exc:
            raise NetworkError(str(exc), self.provider_name, exc) from exc
        files = []
        for row in payload.get("files") or []:
            name = row.get("name") or row.get("$id") or ""
            if prefix and not name.startswith(prefix):
                continue
            files.append(
                FileListEntry(
                    key=name,
                    size=int(row.get("sizeOriginal") or 0),
                    last_modified=_parse_date(row.get("$updatedAt") or row.get("$createdAt")),
                    etag=row.get("$id"),
                )
            )
        next_cursor = None
        total = int(payload.get("total") or 0)
        if files and len(payload.get("files") or []) >= limit:
            next_cursor = (payload.get("files") or [])[-1].get("$id")
            if total and len(files) >= total:
                next_cursor = None
        return ListResult(files=files, next_cursor=next_cursor)

    def _get_url(self, key: str) -> str:
        file_id = file_id_for_key(key)
        encoded = quote(file_id, safe="")
        if self.custom_domain:
            return f"{self.custom_domain.rstrip('/')}/{encoded}"
        if self.cdn_url:
            return f"{self.cdn_url.rstrip('/')}/{encoded}"
        return (
            f"{self.endpoint}/storage/buckets/{self.bucket_id}/files/{encoded}/view"
            f"?project={quote(self.project_id)}"
        )

    def _get_signed_url(self, key: str, options: Optional[SignedUrlOptions] = None) -> str:
        # Appwrite file view URLs require the project query. Time-limited JWTs are
        # not available on the storage REST surface the same way S3 presigns are.
        return self._get_url(key)

    def _copy(self, options: CopyOptions) -> UploadResult:
        return self.copy_via_download(options)

    def _get_stream(self, key: str) -> BinaryIO:
        file_id = file_id_for_key(key)
        try:
            response = self.http.get(self._file_url(file_id, "/download"))
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


def _raise_http(response: Any, provider: str, key: Optional[str] = None) -> None:
    status = getattr(response, "status_code", 500)
    if status < 400:
        return
    body = getattr(response, "text", "") or str(status)
    if status == 404:
        if key:
            raise FileNotFoundError(key, provider)
        raise ProviderError(body, provider, status_code=404)
    if status in {401, 403}:
        raise PermissionError(body, provider)
    if status in {408, 429, 500, 502, 503, 504}:
        raise NetworkError(body, provider)
    raise ProviderError(body, provider, status_code=status)
