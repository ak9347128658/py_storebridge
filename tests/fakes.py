"""In-memory provider used by the unit tests."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import BinaryIO, Dict, Optional

from py_storebridge.config import LogLevel, RetryConfig, Visibility
from py_storebridge.exceptions import FileNotFoundError
from py_storebridge.io import resolve_body
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


class MemoryProvider(BaseProvider):
    provider_name = "memory"

    def __init__(
        self,
        name: str = "memory",
        *,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
    ) -> None:
        super().__init__(retry=retry, log_level=log_level, default_visibility=default_visibility)
        self.provider_name = name
        self._store: Dict[str, dict] = {}

    def _put(self, key: str, data: bytes, options: UploadOptions) -> UploadResult:
        visibility = self.resolve_visibility(options.visibility)
        record = {
            "data": data,
            "content_type": options.content_type or "application/octet-stream",
            "metadata": dict(options.metadata or {}),
            "visibility": visibility,
            "etag": hashlib.md5(data).hexdigest(),
            "last_modified": datetime.now(timezone.utc),
        }
        self._store[key] = record
        return UploadResult(
            key=key,
            url=self._get_url(key),
            provider=self.provider_name,
            etag=record["etag"],
            size=len(data),
            content_type=record["content_type"],
            metadata=record["metadata"] or None,
        )

    def _upload(self, options: UploadOptions) -> UploadResult:
        body, _ = resolve_body(options.file, options.path)
        data = body if isinstance(body, bytes) else body.read()
        return self._put(options.key, data, options)

    def _delete(self, key: str) -> None:
        self._store.pop(key, None)

    def _exists(self, key: str) -> bool:
        return key in self._store

    def _require(self, key: str) -> dict:
        if key not in self._store:
            raise FileNotFoundError(key, self.provider_name)
        return self._store[key]

    def _get_metadata(self, key: str) -> FileMetadata:
        record = self._require(key)
        return FileMetadata(
            key=key,
            size=len(record["data"]),
            content_type=record["content_type"],
            last_modified=record["last_modified"],
            etag=record["etag"],
            metadata=record["metadata"] or None,
            visibility=record["visibility"],
        )

    def _list(self, options: Optional[ListOptions] = None) -> ListResult:
        prefix = options.prefix if options else None
        delimiter = options.delimiter if options else None
        limit = options.limit if options else None
        cursor = options.cursor if options else None
        keys = sorted(self._store)
        if prefix:
            keys = [key for key in keys if key.startswith(prefix)]
        if cursor:
            keys = [key for key in keys if key > cursor]
        prefixes = []
        files = []
        if delimiter:
            seen = set()
            for key in keys:
                rest = key[len(prefix or "") :]
                if delimiter in rest:
                    folder = (prefix or "") + rest.split(delimiter, 1)[0] + delimiter
                    if folder not in seen:
                        seen.add(folder)
                        prefixes.append(folder)
                    continue
                files.append(key)
        else:
            files = keys
        truncated = False
        if limit is not None and len(files) > limit:
            files = files[:limit]
            truncated = True
        entries = [
            FileListEntry(
                key=key,
                size=len(self._store[key]["data"]),
                last_modified=self._store[key]["last_modified"],
                etag=self._store[key]["etag"],
            )
            for key in files
        ]
        next_cursor = files[-1] if truncated and files else None
        return ListResult(files=entries, next_cursor=next_cursor, prefixes=prefixes or None)

    def _get_url(self, key: str) -> str:
        return f"memory://{self.provider_name}/{key}"

    def _get_signed_url(self, key: str, options: Optional[SignedUrlOptions] = None) -> str:
        self._require(key)
        expires = (options.expires_in if options else 3600)
        return f"{self._get_url(key)}?expires={expires}&method={(options.method if options else 'GET')}"

    def _copy(self, options: CopyOptions) -> UploadResult:
        record = self._require(options.source_key)
        new_options = UploadOptions(
            key=options.destination_key,
            file=record["data"],
            content_type=options.content_type or record["content_type"],
            visibility=options.visibility or record["visibility"],
            metadata=options.metadata if options.metadata is not None else record["metadata"],
        )
        return self._put(options.destination_key, record["data"], new_options)

    def _get_stream(self, key: str) -> BinaryIO:
        record = self._require(key)
        return as_bytes_io(record["data"])
