"""Public StorageClient wrapping a provider with hooks and middleware."""

from __future__ import annotations

from typing import Any, BinaryIO, Iterator, List, Optional, Union

from py_storebridge.exceptions import StoreBridgeError
from py_storebridge.hooks import HookHandler, HookSystem
from py_storebridge.middleware import Middleware, MiddlewareChain, MiddlewareContext
from py_storebridge.providers.base import BaseProvider
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
    TransferOptions,
    TransferResult,
    UploadOptions,
    UploadPartOptions,
    UploadResult,
    parse_model,
)


class StorageClient:
    """Unified storage surface. Construct via ``create_storage()``."""

    def __init__(self, provider: BaseProvider) -> None:
        self._provider = provider
        self._hooks = HookSystem()
        self._middleware = MiddlewareChain()

    @property
    def provider_name(self) -> str:
        return self._provider.provider_name

    @property
    def provider(self) -> BaseProvider:
        return self._provider

    def use(self, middleware: Middleware) -> "StorageClient":
        self._middleware.use(middleware)
        return self

    def on(self, event: str, handler: HookHandler) -> "StorageClient":
        self._hooks.on(event, handler)
        return self

    def off(self, event: str, handler: HookHandler) -> "StorageClient":
        self._hooks.off(event, handler)
        return self

    def upload(self, options: Union[UploadOptions, dict, None] = None, **kwargs: Any) -> UploadResult:
        parsed = parse_model(UploadOptions, options, **kwargs)
        self._hooks.emit("before:upload", parsed)
        try:
            result = self._middleware.execute(
                MiddlewareContext(options=parsed, provider=self.provider_name),
                lambda ctx: self._provider.upload(ctx.options),
            )
        except StoreBridgeError as exc:
            self._hooks.emit("error", {"operation": "upload", "error": exc})
            raise
        self._hooks.emit("after:upload", result)
        return result

    def delete(self, options: Union[DeleteOptions, dict, None] = None, **kwargs: Any) -> None:
        parsed = parse_model(DeleteOptions, options, **kwargs)
        self._hooks.emit("before:delete", parsed)
        try:
            self._provider.delete(parsed)
        except StoreBridgeError as exc:
            self._hooks.emit("error", {"operation": "delete", "error": exc})
            raise
        self._hooks.emit("after:delete", parsed)

    def exists(self, key: str) -> bool:
        return self._provider.exists(key)

    def get_metadata(self, key: str) -> FileMetadata:
        return self._provider.get_metadata(key)

    def list(self, options: Union[ListOptions, dict, None] = None, **kwargs: Any) -> ListResult:
        parsed = parse_model(ListOptions, options or kwargs or {})
        self._hooks.emit("before:list", {"prefix": parsed.prefix})
        try:
            result = self._provider.list(parsed)
        except StoreBridgeError as exc:
            self._hooks.emit("error", {"operation": "list", "error": exc})
            raise
        self._hooks.emit("after:list", result)
        return result

    def iter_files(self, prefix: Optional[str] = None, page_size: int = 1000) -> Iterator[FileListEntry]:
        return self._provider.iter_files(prefix=prefix, page_size=page_size)

    def get_url(self, key: str) -> str:
        return self._provider.get_url(key)

    def get_signed_url(
        self,
        key: str,
        options: Union[SignedUrlOptions, dict, None] = None,
        **kwargs: Any,
    ) -> str:
        parsed = parse_model(SignedUrlOptions, options, **kwargs) if options or kwargs else SignedUrlOptions()
        self._hooks.emit("before:getSignedUrl", {"key": key, "expires_in": parsed.expires_in})
        try:
            url = self._provider.get_signed_url(key, parsed)
        except StoreBridgeError as exc:
            self._hooks.emit("error", {"operation": "getSignedUrl", "error": exc})
            raise
        self._hooks.emit("after:getSignedUrl", {"key": key, "url": url})
        return url

    def copy(self, options: Union[CopyOptions, dict, None] = None, **kwargs: Any) -> UploadResult:
        parsed = parse_model(CopyOptions, options, **kwargs)
        self._hooks.emit(
            "before:copy",
            {"source_key": parsed.source_key, "destination_key": parsed.destination_key},
        )
        try:
            result = self._provider.copy(parsed)
        except StoreBridgeError as exc:
            self._hooks.emit("error", {"operation": "copy", "error": exc})
            raise
        self._hooks.emit("after:copy", result)
        return result

    def move(self, options: Union[CopyOptions, dict, None] = None, **kwargs: Any) -> UploadResult:
        parsed = parse_model(CopyOptions, options, **kwargs)
        self._hooks.emit(
            "before:move",
            {"source_key": parsed.source_key, "destination_key": parsed.destination_key},
        )
        try:
            result = self._provider.move(parsed)
        except StoreBridgeError as exc:
            self._hooks.emit("error", {"operation": "move", "error": exc})
            raise
        self._hooks.emit("after:move", result)
        return result

    def rename(self, source_key: str, destination_key: str) -> UploadResult:
        return self.move(source_key=source_key, destination_key=destination_key)

    def get_stream(self, key: str) -> BinaryIO:
        return self._provider.get_stream(key)

    def get_buffer(self, key: str) -> bytes:
        return self._provider.get_buffer(key)

    def download_file(self, key: str, destination: Union[str, Any]) -> None:
        from pathlib import Path

        data = self.get_buffer(key)
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def upload_file(self, path: Union[str, Any], key: Optional[str] = None, **kwargs: Any) -> UploadResult:
        from pathlib import Path

        file_path = Path(path)
        object_key = key or file_path.name
        return self.upload(key=object_key, path=file_path, **kwargs)

    def create_multipart_upload(self, key: str, content_type: Optional[str] = None) -> MultipartUploadSession:
        return self._provider.create_multipart_upload(key, content_type)

    def upload_part(self, options: Union[UploadPartOptions, dict, None] = None, **kwargs: Any) -> MultipartPartResult:
        parsed = parse_model(UploadPartOptions, options, **kwargs)
        return self._provider.upload_part(parsed)

    def complete_multipart_upload(
        self,
        session: MultipartUploadSession,
        parts: List[MultipartPartResult],
    ) -> UploadResult:
        return self._provider.complete_multipart_upload(session, parts)

    def abort_multipart_upload(self, session: MultipartUploadSession) -> None:
        self._provider.abort_multipart_upload(session)

    def list_parts(self, session: MultipartUploadSession) -> List[FileListEntry]:
        return self._provider.list_parts(session)

    def upload_multipart(
        self,
        options: Union[UploadOptions, dict, None] = None,
        chunk_size: int = 5 * 1024 * 1024,
        **kwargs: Any,
    ) -> UploadResult:
        parsed = parse_model(UploadOptions, options, **kwargs)
        return self._provider.upload_multipart(parsed, chunk_size=chunk_size)

    def delete_prefix(self, prefix: str, *, continue_on_error: bool = True) -> int:
        """Delete every object under ``prefix``. Returns the number deleted."""
        deleted = 0
        for entry in self.iter_files(prefix=prefix):
            try:
                self.delete(key=entry.key)
                deleted += 1
            except StoreBridgeError:
                if not continue_on_error:
                    raise
        return deleted

    def transfer_to(
        self,
        destination: "StorageClient",
        options: Union[TransferOptions, dict, None] = None,
        **kwargs: Any,
    ) -> TransferResult:
        from py_storebridge.transfer import transfer

        return transfer(self, destination, options, **kwargs)
