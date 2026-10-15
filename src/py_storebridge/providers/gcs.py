from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, BinaryIO, Optional
from urllib.parse import quote

from py_storebridge.config import GCSCredentials, LogLevel, RetryConfig, Visibility
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
    SignedUrlOptions,
    UploadOptions,
    UploadResult,
)


class GCSProvider(BaseProvider):
    provider_name = "gcs"
    extra_name = "gcs"

    def __init__(
        self,
        credentials: GCSCredentials,
        *,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
        client: Any = None,
    ) -> None:
        super().__init__(retry=retry, log_level=log_level, default_visibility=default_visibility)
        self.bucket_name = credentials.bucket
        self.project_id = credentials.project_id
        self.cdn_url = credentials.cdn_url
        self.custom_domain = credentials.custom_domain
        storage = require("google.cloud.storage", self.extra_name)
        google_auth = None
        gcs_credentials = None
        if credentials.service_account:
            google_auth = require("google.oauth2.service_account", self.extra_name)
            gcs_credentials = google_auth.Credentials.from_service_account_info(credentials.service_account)
        elif credentials.credentials is not None:
            google_auth = require("google.oauth2.service_account", self.extra_name)
            info = {
                "type": "service_account",
                "project_id": credentials.project_id,
                "client_email": credentials.credentials.client_email,
                "private_key": credentials.credentials.private_key.replace("\\n", "\n"),
                "token_uri": "https://oauth2.googleapis.com/token",
            }
            gcs_credentials = google_auth.Credentials.from_service_account_info(info)
        if client is not None:
            self.client = client
        else:
            kwargs: dict[str, Any] = {"project": credentials.project_id}
            if gcs_credentials is not None:
                kwargs["credentials"] = gcs_credentials
            elif credentials.key_filename:
                kwargs["client_options"] = None
                self.client = storage.Client.from_service_account_json(
                    credentials.key_filename, project=credentials.project_id
                )
                self.bucket = self.client.bucket(self.bucket_name)
                return
            self.client = storage.Client(**kwargs)
        self.bucket = self.client.bucket(self.bucket_name)

    def _blob(self, key: str):
        return self.bucket.blob(key)

    def _upload(self, options: UploadOptions) -> UploadResult:
        body, size = resolve_body(options.file, options.path)
        blob = self._blob(options.key)
        blob.content_type = options.content_type
        if options.cache_control:
            blob.cache_control = options.cache_control
        if options.content_disposition:
            blob.content_disposition = options.content_disposition
        if options.content_encoding:
            blob.content_encoding = options.content_encoding
        if options.metadata:
            blob.metadata = options.metadata
        try:
            if isinstance(body, bytes):
                blob.upload_from_string(body, content_type=options.content_type)
            else:
                blob.upload_from_file(body, content_type=options.content_type, rewind=True)
            visibility = self.resolve_visibility(options.visibility)
            if visibility == Visibility.PUBLIC:
                try:
                    blob.make_public()
                except Exception:  # noqa: BLE001 — uniform buckets skip ACLs
                    pass
        except Exception as exc:
            raise _translate(exc, self.provider_name, options.key) from exc
        return UploadResult(
            key=options.key,
            url=self._get_url(options.key),
            provider=self.provider_name,
            size=size,
            content_type=options.content_type,
            metadata=options.metadata,
            etag=getattr(blob, "etag", None),
        )

    def _delete(self, key: str) -> None:
        try:
            self._blob(key).delete()
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
        blob = self._blob(key)
        try:
            blob.reload()
        except Exception as exc:
            raise _translate(exc, self.provider_name, key) from exc
        visibility = None
        try:
            if blob.public_url and getattr(blob, "acl", None):
                visibility = Visibility.PUBLIC if _blob_is_public(blob) else Visibility.PRIVATE
        except Exception:  # noqa: BLE001
            visibility = None
        return FileMetadata(
            key=key,
            size=int(blob.size or 0),
            content_type=blob.content_type or "application/octet-stream",
            last_modified=blob.updated or datetime.now(timezone.utc),
            etag=blob.etag,
            metadata=dict(blob.metadata) if blob.metadata else None,
            visibility=visibility,
        )

    def _list(self, options: Optional[ListOptions] = None) -> ListResult:
        prefix = options.prefix if options else None
        delimiter = options.delimiter if options else None
        max_results = options.limit if options else None
        page_token = options.cursor if options else None
        try:
            iterator = self.client.list_blobs(
                self.bucket_name,
                prefix=prefix,
                delimiter=delimiter,
                max_results=max_results,
                page_token=page_token,
            )
            blobs = list(iterator)
            prefixes = list(getattr(iterator, "prefixes", []) or [])
            next_token = getattr(iterator, "next_page_token", None)
        except Exception as exc:
            raise _translate(exc, self.provider_name) from exc
        files = [
            FileListEntry(
                key=blob.name,
                size=int(blob.size or 0),
                last_modified=blob.updated or datetime.now(timezone.utc),
                etag=blob.etag,
            )
            for blob in blobs
            if blob.name
        ]
        return ListResult(files=files, next_cursor=next_token, prefixes=prefixes or None)

    def _get_url(self, key: str) -> str:
        encoded = quote(key, safe="/")
        if self.custom_domain:
            return f"{self.custom_domain.rstrip('/')}/{encoded}"
        if self.cdn_url:
            return f"{self.cdn_url.rstrip('/')}/{encoded}"
        return f"https://storage.googleapis.com/{self.bucket_name}/{encoded}"

    def _get_signed_url(self, key: str, options: Optional[SignedUrlOptions] = None) -> str:
        opts = options or SignedUrlOptions()
        blob = self._blob(key)
        kwargs: dict[str, Any] = {
            "version": "v4",
            "expiration": timedelta(seconds=opts.expires_in),
            "method": opts.method,
        }
        if opts.method == "PUT" and opts.content_type:
            kwargs["content_type"] = opts.content_type
        if opts.query_params:
            kwargs["query_parameters"] = opts.query_params
        try:
            return blob.generate_signed_url(**kwargs)
        except Exception as exc:
            raise _translate(exc, self.provider_name, key) from exc

    def _copy(self, options: CopyOptions) -> UploadResult:
        source = self._blob(options.source_key)
        try:
            self.bucket.copy_blob(source, self.bucket, options.destination_key)
            dest = self._blob(options.destination_key)
            if options.content_type or options.metadata:
                dest.reload()
                if options.content_type:
                    dest.content_type = options.content_type
                if options.metadata:
                    dest.metadata = options.metadata
                dest.patch()
            visibility = options.visibility
            if visibility == Visibility.PUBLIC:
                try:
                    dest.make_public()
                except Exception:  # noqa: BLE001
                    pass
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
        blob = self._blob(key)
        try:
            data = blob.download_as_bytes()
        except Exception as exc:
            raise _translate(exc, self.provider_name, key) from exc
        return as_bytes_io(data)


def _blob_is_public(blob: Any) -> bool:
    try:
        for entry in blob.acl:
            if entry.get("entity") == "allUsers" and entry.get("role") == "READER":
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def _is_not_found(exc: BaseException) -> bool:
    code = getattr(exc, "code", None)
    name = exc.__class__.__name__
    return code == 404 or name in {"NotFound", "NotFoundError"}


def _translate(exc: BaseException, provider: str, key: Optional[str] = None) -> Exception:
    from py_storebridge.exceptions import StoreBridgeError

    if isinstance(exc, StoreBridgeError):
        return exc
    code = getattr(exc, "code", None)
    name = exc.__class__.__name__
    if code == 404 or name in {"NotFound", "NotFoundError"}:
        if key:
            return FileNotFoundError(key, provider, exc)
        return ProviderError(str(exc), provider, exc, 404)
    if code == 403 or name in {"Forbidden", "PermissionDenied"}:
        return PermissionError(str(exc), provider, exc)
    return ProviderError(str(exc), provider, exc, int(code or 500))
