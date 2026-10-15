from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, BinaryIO, Optional
from urllib.parse import quote

from py_storebridge.config import AzureCredentials, LogLevel, RetryConfig, Visibility
from py_storebridge.exceptions import FileNotFoundError, PermissionError, ProviderError
from py_storebridge.io import resolve_body
from py_storebridge.providers._deps import require
from py_storebridge.providers.base import BaseProvider, as_bytes_io
from py_storebridge.schemas import (
    CopyOptions,
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


class AzureProvider(BaseProvider):
    provider_name = "azure"
    extra_name = "azure"

    def __init__(
        self,
        credentials: AzureCredentials,
        *,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
        client: Any = None,
    ) -> None:
        super().__init__(retry=retry, log_level=log_level, default_visibility=default_visibility)
        self.account_name = credentials.account_name
        self.account_key = credentials.account_key
        self.container_name = credentials.container_name
        self.cdn_url = credentials.cdn_url
        self.custom_domain = credentials.custom_domain
        blob_mod = require("azure.storage.blob", self.extra_name)
        if client is not None:
            self.service = client
        elif credentials.connection_string:
            self.service = blob_mod.BlobServiceClient.from_connection_string(credentials.connection_string)
        else:
            account_url = f"https://{credentials.account_name}.blob.core.windows.net"
            self.service = blob_mod.BlobServiceClient(account_url=account_url, credential=credentials.account_key)
        self.container = self.service.get_container_client(self.container_name)
        self._blob_mod = blob_mod

    def _blob(self, key: str):
        return self.container.get_blob_client(key)

    def _upload(self, options: UploadOptions) -> UploadResult:
        body, size = resolve_body(options.file, options.path)
        content_settings = self._blob_mod.ContentSettings(
            content_type=options.content_type,
            content_disposition=options.content_disposition,
            content_encoding=options.content_encoding,
            cache_control=options.cache_control,
        )
        try:
            self._blob(options.key).upload_blob(
                body,
                overwrite=True,
                content_settings=content_settings,
                metadata=options.metadata,
            )
        except Exception as exc:
            raise _translate(exc, self.provider_name, options.key) from exc
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
            self._blob(key).delete_blob()
        except Exception as exc:
            if _is_not_found(exc):
                return
            raise _translate(exc, self.provider_name, key) from exc

    def _exists(self, key: str) -> bool:
        try:
            return bool(self._blob(key).exists())
        except Exception as exc:
            raise _translate(exc, self.provider_name, key) from exc

    def _get_metadata(self, key: str) -> FileMetadata:
        try:
            props = self._blob(key).get_blob_properties()
        except Exception as exc:
            raise _translate(exc, self.provider_name, key) from exc
        content_type = "application/octet-stream"
        if props.content_settings and props.content_settings.content_type:
            content_type = props.content_settings.content_type
        return FileMetadata(
            key=key,
            size=int(props.size or 0),
            content_type=content_type,
            last_modified=props.last_modified or datetime.now(timezone.utc),
            etag=_clean_etag(getattr(props, "etag", None)),
            metadata=dict(props.metadata) if props.metadata else None,
        )

    def _list(self, options: Optional[ListOptions] = None) -> ListResult:
        name_starts_with = options.prefix if options else None
        results_per_page = options.limit if options else None
        delimiter = options.delimiter if options else None
        try:
            pager = self.container.list_blobs(
                name_starts_with=name_starts_with,
                results_per_page=results_per_page,
                delimiter=delimiter,
            )
            if options and options.cursor:
                pages = pager.by_page(continuation_token=options.cursor)
            else:
                pages = pager.by_page()
            page = next(pages)
            files = [
                FileListEntry(
                    key=blob.name,
                    size=int(getattr(blob, "size", 0) or 0),
                    last_modified=getattr(blob, "last_modified", None) or datetime.now(timezone.utc),
                    etag=_clean_etag(getattr(blob, "etag", None)),
                )
                for blob in page
                if getattr(blob, "name", None)
            ]
            next_cursor = pages.continuation_token
            prefixes = None
            if delimiter:
                prefixes = list(getattr(pager, "prefix", []) or [])
        except Exception as exc:
            raise _translate(exc, self.provider_name) from exc
        return ListResult(files=files, next_cursor=next_cursor or None, prefixes=prefixes)

    def _get_url(self, key: str) -> str:
        encoded = quote(key, safe="/")
        if self.custom_domain:
            return f"{self.custom_domain.rstrip('/')}/{encoded}"
        if self.cdn_url:
            return f"{self.cdn_url.rstrip('/')}/{encoded}"
        return f"https://{self.account_name}.blob.core.windows.net/{self.container_name}/{encoded}"

    def _get_signed_url(self, key: str, options: Optional[SignedUrlOptions] = None) -> str:
        opts = options or SignedUrlOptions()
        if not self.account_key:
            raise ProviderError(
                "Signed URLs require account_key on Azure credentials",
                self.provider_name,
            )
        generate_blob_sas = self._blob_mod.generate_blob_sas
        BlobSasPermissions = self._blob_mod.BlobSasPermissions
        permission = BlobSasPermissions(
            read=opts.method == "GET",
            write=opts.method == "PUT",
            delete=opts.method == "DELETE",
        )
        try:
            token = generate_blob_sas(
                account_name=self.account_name,
                container_name=self.container_name,
                blob_name=key,
                account_key=self.account_key,
                permission=permission,
                expiry=datetime.now(timezone.utc) + timedelta(seconds=opts.expires_in),
                content_type=opts.content_type if opts.method == "PUT" else None,
            )
        except Exception as exc:
            raise _translate(exc, self.provider_name, key) from exc
        return f"{self._get_url(key)}?{token}"

    def _copy(self, options: CopyOptions) -> UploadResult:
        source = self._blob(options.source_key)
        dest = self._blob(options.destination_key)
        try:
            dest.start_copy_from_url(source.url)
            if options.metadata:
                dest.set_blob_metadata(options.metadata)
        except Exception as exc:
            raise _translate(exc, self.provider_name, options.source_key) from exc
        return UploadResult(
            key=options.destination_key,
            url=self._get_url(options.destination_key),
            provider=self.provider_name,
            content_type=options.content_type,
            metadata=options.metadata,
        )

    def _get_stream(self, key: str) -> BinaryIO:
        try:
            downloader = self._blob(key).download_blob()
            data = downloader.readall()
        except Exception as exc:
            raise _translate(exc, self.provider_name, key) from exc
        return as_bytes_io(data)

    def _create_multipart_upload(self, key: str, content_type: Optional[str] = None) -> MultipartUploadSession:
        session = super()._create_multipart_upload(key, content_type)
        return session

    def _upload_part(self, options: UploadPartOptions) -> MultipartPartResult:
        body, _ = resolve_body(options.body)
        data = body if isinstance(body, bytes) else body.read()
        block_id = f"{options.part_number:08d}".encode("utf-8").hex()
        try:
            self._blob(options.key).stage_block(block_id, data)
        except Exception as exc:
            raise _translate(exc, self.provider_name, options.key) from exc
        with self._multipart_lock:
            stored = self._multipart.setdefault(
                options.upload_id,
                {"key": options.key, "content_type": None, "parts": {}, "block_ids": {}},
            )
            stored["parts"][options.part_number] = data  # type: ignore[index]
            stored.setdefault("block_ids", {})[options.part_number] = block_id  # type: ignore[index]
        return MultipartPartResult(part_number=options.part_number, etag=block_id)

    def _complete_multipart_upload(
        self,
        session: MultipartUploadSession,
        parts: list[MultipartPartResult],
    ) -> UploadResult:
        with self._multipart_lock:
            stored = self._multipart.pop(session.upload_id, None)
        if stored is None:
            return super()._complete_multipart_upload(session, parts)
        block_ids = [stored.get("block_ids", {}).get(part.part_number) or part.etag for part in sorted(parts, key=lambda p: p.part_number)]  # type: ignore[union-attr]
        content_settings = self._blob_mod.ContentSettings(
            content_type=stored.get("content_type"),
        )
        try:
            self._blob(session.key).commit_block_list(block_ids, content_settings=content_settings)
        except Exception as exc:
            raise _translate(exc, self.provider_name, session.key) from exc
        return UploadResult(key=session.key, url=self._get_url(session.key), provider=self.provider_name)


def _clean_etag(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return str(value).strip('"')


def _is_not_found(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    name = exc.__class__.__name__
    return status == 404 or name in {"ResourceNotFoundError"}


def _translate(exc: BaseException, provider: str, key: Optional[str] = None) -> Exception:
    from py_storebridge.exceptions import StoreBridgeError

    if isinstance(exc, StoreBridgeError):
        return exc
    status = getattr(exc, "status_code", None)
    name = exc.__class__.__name__
    if status == 404 or name == "ResourceNotFoundError":
        if key:
            return FileNotFoundError(key, provider, exc)
        return ProviderError(str(exc), provider, exc, 404)
    if status == 403 or name in {"ClientAuthenticationError", "HttpResponseError"} and status == 403:
        return PermissionError(str(exc), provider, exc)
    return ProviderError(str(exc), provider, exc, int(status or 500))
