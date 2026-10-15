"""Abstract provider with retry, MIME detection, and multipart fallback."""

from __future__ import annotations

import io
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import BinaryIO, Dict, List, Optional
from urllib.parse import quote

from py_storebridge.config import LogLevel, RetryConfig, Visibility
from py_storebridge.exceptions import MultipartUploadError
from py_storebridge.io import read_all, resolve_body, split_bytes, stream_to_bytes
from py_storebridge.keys import validate_key
from py_storebridge.logging import Logger
from py_storebridge.mime import detect_mime_type
from py_storebridge.retry import merge_retry_config, with_retry
from py_storebridge.schemas import (
    CopyOptions,
    DeleteOptions,
    FileListEntry,
    FileMetadata,
    ListOptions,
    ListResult,
    MultipartPartResult,
    MultipartUploadSession,
    SignedUrlOptions,
    UploadOptions,
    UploadPartOptions,
    UploadResult,
)


class BaseProvider(ABC):
    """Every backend implements the ``_`` methods; this class wraps them."""

    provider_name: str = "base"

    def __init__(
        self,
        *,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
    ) -> None:
        self.retry_config = merge_retry_config(retry)
        self.logger = Logger(log_level or LogLevel.WARN)
        self.default_visibility = default_visibility or Visibility.PRIVATE
        self._multipart: Dict[str, Dict[str, object]] = {}
        self._multipart_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Abstract backend operations
    # ------------------------------------------------------------------

    @abstractmethod
    def _upload(self, options: UploadOptions) -> UploadResult:
        raise NotImplementedError

    @abstractmethod
    def _delete(self, key: str) -> None:
        raise NotImplementedError

    @abstractmethod
    def _exists(self, key: str) -> bool:
        raise NotImplementedError

    @abstractmethod
    def _get_metadata(self, key: str) -> FileMetadata:
        raise NotImplementedError

    @abstractmethod
    def _list(self, options: Optional[ListOptions] = None) -> ListResult:
        raise NotImplementedError

    @abstractmethod
    def _get_url(self, key: str) -> str:
        raise NotImplementedError

    @abstractmethod
    def _get_signed_url(self, key: str, options: Optional[SignedUrlOptions] = None) -> str:
        raise NotImplementedError

    @abstractmethod
    def _copy(self, options: CopyOptions) -> UploadResult:
        raise NotImplementedError

    @abstractmethod
    def _get_stream(self, key: str) -> BinaryIO:
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def upload(self, options: UploadOptions) -> UploadResult:
        if not options.content_type:
            options.content_type = detect_mime_type(options.key)
        self.logger.debug("upload:start", key=options.key, provider=self.provider_name)
        result = with_retry(
            lambda: self._upload(options),
            self.retry_config,
            self.provider_name,
            self.logger,
        )
        self.logger.info("upload:complete", key=options.key, url=result.url)
        return result

    def delete(self, options: DeleteOptions) -> None:
        self.logger.debug("delete:start", key=options.key)
        with_retry(
            lambda: self._delete(options.key),
            self.retry_config,
            self.provider_name,
            self.logger,
        )
        self.logger.info("delete:complete", key=options.key)

    def exists(self, key: str) -> bool:
        validate_key(key)
        return with_retry(
            lambda: self._exists(key),
            self.retry_config,
            self.provider_name,
            self.logger,
        )

    def get_metadata(self, key: str) -> FileMetadata:
        validate_key(key)
        return with_retry(
            lambda: self._get_metadata(key),
            self.retry_config,
            self.provider_name,
            self.logger,
        )

    def list(self, options: Optional[ListOptions] = None) -> ListResult:
        return with_retry(
            lambda: self._list(options),
            self.retry_config,
            self.provider_name,
            self.logger,
        )

    def get_url(self, key: str) -> str:
        validate_key(key)
        return with_retry(
            lambda: self._get_url(key),
            self.retry_config,
            self.provider_name,
            self.logger,
        )

    def get_signed_url(self, key: str, options: Optional[SignedUrlOptions] = None) -> str:
        validate_key(key)
        return with_retry(
            lambda: self._get_signed_url(key, options),
            self.retry_config,
            self.provider_name,
            self.logger,
        )

    def copy(self, options: CopyOptions) -> UploadResult:
        return with_retry(
            lambda: self._copy(options),
            self.retry_config,
            self.provider_name,
            self.logger,
        )

    def move(self, options: CopyOptions) -> UploadResult:
        result = self.copy(options)
        self.delete(DeleteOptions(key=options.source_key))
        return result

    def rename(self, source_key: str, destination_key: str) -> UploadResult:
        return self.move(CopyOptions(source_key=source_key, destination_key=destination_key))

    def get_stream(self, key: str) -> BinaryIO:
        validate_key(key)
        return with_retry(
            lambda: self._get_stream(key),
            self.retry_config,
            self.provider_name,
            self.logger,
        )

    def get_buffer(self, key: str) -> bytes:
        stream = self.get_stream(key)
        try:
            return stream_to_bytes(stream)
        finally:
            closer = getattr(stream, "close", None)
            if callable(closer):
                closer()

    def create_multipart_upload(self, key: str, content_type: Optional[str] = None) -> MultipartUploadSession:
        validate_key(key)
        mime = content_type or detect_mime_type(key)
        return with_retry(
            lambda: self._create_multipart_upload(key, mime),
            self.retry_config,
            self.provider_name,
            self.logger,
        )

    def upload_part(self, options: UploadPartOptions) -> MultipartPartResult:
        return with_retry(
            lambda: self._upload_part(options),
            self.retry_config,
            self.provider_name,
            self.logger,
        )

    def complete_multipart_upload(
        self,
        session: MultipartUploadSession,
        parts: List[MultipartPartResult],
    ) -> UploadResult:
        return with_retry(
            lambda: self._complete_multipart_upload(session, parts),
            self.retry_config,
            self.provider_name,
            self.logger,
        )

    def abort_multipart_upload(self, session: MultipartUploadSession) -> None:
        with_retry(
            lambda: self._abort_multipart_upload(session),
            self.retry_config,
            self.provider_name,
            self.logger,
        )

    def list_parts(self, session: MultipartUploadSession) -> List[FileListEntry]:
        return with_retry(
            lambda: self._list_parts(session),
            self.retry_config,
            self.provider_name,
            self.logger,
        )

    def upload_multipart(self, options: UploadOptions, chunk_size: int = 5 * 1024 * 1024) -> UploadResult:
        data = read_all(options.file, options.path)
        content_type = options.content_type or detect_mime_type(options.key)
        session = self.create_multipart_upload(options.key, content_type)
        chunks = split_bytes(data, chunk_size)
        parts: List[MultipartPartResult] = []
        uploaded = 0
        try:
            for index, chunk in enumerate(chunks, start=1):
                part = self.upload_part(
                    UploadPartOptions(
                        upload_id=session.upload_id,
                        key=session.key,
                        part_number=index,
                        body=chunk,
                    )
                )
                parts.append(part)
                uploaded += len(chunk)
                if options.on_progress:
                    options.on_progress(uploaded, len(data))
            return self.complete_multipart_upload(session, parts)
        except Exception:
            try:
                self.abort_multipart_upload(session)
            except Exception:  # noqa: BLE001
                self.logger.warn("multipart:abort-failed", key=options.key)
            raise

    def iter_files(self, prefix: Optional[str] = None, page_size: int = 1000):
        cursor = None
        while True:
            result = self.list(
                ListOptions(prefix=prefix, limit=page_size, cursor=cursor)
            )
            for entry in result.files:
                yield entry
            if not result.next_cursor:
                return
            cursor = result.next_cursor

    # ------------------------------------------------------------------
    # Multipart fallback used by backends without native multipart
    # ------------------------------------------------------------------

    def _create_multipart_upload(self, key: str, content_type: Optional[str] = None) -> MultipartUploadSession:
        upload_id = uuid.uuid4().hex
        with self._multipart_lock:
            self._multipart[upload_id] = {
                "key": key,
                "content_type": content_type,
                "parts": {},
            }
        return MultipartUploadSession(upload_id=upload_id, key=key)

    def _upload_part(self, options: UploadPartOptions) -> MultipartPartResult:
        body, _ = resolve_body(options.body)
        data = body if isinstance(body, bytes) else body.read()
        with self._multipart_lock:
            session = self._multipart.get(options.upload_id)
            if session is None:
                raise MultipartUploadError(
                    f'Unknown multipart session "{options.upload_id}"',
                    self.provider_name,
                )
            parts: Dict[int, bytes] = session["parts"]  # type: ignore[assignment]
            parts[options.part_number] = data
        etag = _etag(data)
        return MultipartPartResult(part_number=options.part_number, etag=etag)

    def _complete_multipart_upload(
        self,
        session: MultipartUploadSession,
        parts: List[MultipartPartResult],
    ) -> UploadResult:
        with self._multipart_lock:
            stored = self._multipart.pop(session.upload_id, None)
        if stored is None:
            raise MultipartUploadError(
                f'Unknown multipart session "{session.upload_id}"',
                self.provider_name,
            )
        blob_parts: Dict[int, bytes] = stored["parts"]  # type: ignore[assignment]
        ordered = []
        for part in sorted(parts, key=lambda item: item.part_number):
            chunk = blob_parts.get(part.part_number)
            if chunk is None:
                raise MultipartUploadError(
                    f"Missing part {part.part_number} for key {session.key}",
                    self.provider_name,
                )
            ordered.append(chunk)
        data = b"".join(ordered)
        return self._upload(
            UploadOptions(
                key=session.key,
                file=data,
                content_type=stored.get("content_type") or detect_mime_type(session.key),
            )
        )

    def _abort_multipart_upload(self, session: MultipartUploadSession) -> None:
        with self._multipart_lock:
            self._multipart.pop(session.upload_id, None)

    def _list_parts(self, session: MultipartUploadSession) -> List[FileListEntry]:
        with self._multipart_lock:
            stored = self._multipart.get(session.upload_id)
            if stored is None:
                return []
            parts: Dict[int, bytes] = stored["parts"]  # type: ignore[assignment]
            now = datetime.now(timezone.utc)
            return [
                FileListEntry(
                    key=f"{session.key}#part-{number}",
                    size=len(blob),
                    last_modified=now,
                    etag=_etag(blob),
                )
                for number, blob in sorted(parts.items())
            ]

    def resolve_visibility(self, upload_visibility: Optional[Visibility] = None) -> Visibility:
        if upload_visibility is None:
            return self.default_visibility
        return Visibility(upload_visibility)

    def public_url(self, key: str, custom_domain: Optional[str], cdn_url: Optional[str], fallback: str) -> str:
        encoded = quote(key, safe="/")
        if custom_domain:
            return f"{custom_domain.rstrip('/')}/{encoded}"
        if cdn_url:
            return f"{cdn_url.rstrip('/')}/{encoded}"
        return fallback

    def copy_via_download(self, options: CopyOptions) -> UploadResult:
        """Same-provider copy implemented as download + upload."""
        meta = self._get_metadata(options.source_key)
        data = stream_to_bytes(self._get_stream(options.source_key))
        return self._upload(
            UploadOptions(
                key=options.destination_key,
                file=data,
                content_type=options.content_type or meta.content_type,
                visibility=options.visibility or meta.visibility,
                metadata=options.metadata or meta.metadata,
            )
        )


def as_bytes_io(data: bytes) -> BinaryIO:
    return io.BytesIO(data)


def _etag(data: bytes) -> str:
    import hashlib

    return hashlib.md5(data).hexdigest()
