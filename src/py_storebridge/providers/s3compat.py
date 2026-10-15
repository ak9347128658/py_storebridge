"""Shared boto3 adapter for S3-compatible backends (S3, R2, Spaces, B2, MinIO)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, BinaryIO, Optional
from urllib.parse import quote

from py_storebridge.config import LogLevel, RetryConfig, Visibility
from py_storebridge.exceptions import (
    BucketNotFoundError,
    FileNotFoundError,
    NetworkError,
    PermissionError,
    ProviderError,
)
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


class S3CompatibleProvider(BaseProvider):
    extra_name = "s3"

    def __init__(
        self,
        *,
        provider_name: str,
        bucket: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        endpoint: Optional[str] = None,
        force_path_style: bool = False,
        session_token: Optional[str] = None,
        cdn_url: Optional[str] = None,
        custom_domain: Optional[str] = None,
        supports_acl: bool = True,
        extra_name: Optional[str] = None,
        public_url_builder: Optional[Any] = None,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
        client: Any = None,
    ) -> None:
        super().__init__(retry=retry, log_level=log_level, default_visibility=default_visibility)
        self.provider_name = provider_name
        self.extra_name = extra_name or provider_name
        self.bucket = bucket
        self.region = region
        self.endpoint = endpoint.rstrip("/") if endpoint else None
        self.force_path_style = force_path_style
        self.cdn_url = cdn_url
        self.custom_domain = custom_domain
        self.supports_acl = supports_acl
        self._public_url_builder = public_url_builder
        if client is not None:
            self.client = client
        else:
            boto3 = require("boto3", self.extra_name)
            botocore = require("botocore.config", self.extra_name)
            cfg = botocore.Config(
                s3={"addressing_style": "path" if force_path_style else "auto"},
                retries={"max_attempts": 1},
                signature_version="s3v4",
            )
            kwargs: dict[str, Any] = {
                "service_name": "s3",
                "region_name": region,
                "aws_access_key_id": access_key_id,
                "aws_secret_access_key": secret_access_key,
                "config": cfg,
            }
            if session_token:
                kwargs["aws_session_token"] = session_token
            if self.endpoint:
                kwargs["endpoint_url"] = self.endpoint
            self.client = boto3.client(**kwargs)

    def _acl(self, visibility: Visibility) -> Optional[str]:
        if not self.supports_acl:
            return None
        return "public-read" if visibility == Visibility.PUBLIC else "private"

    def _upload(self, options: UploadOptions) -> UploadResult:
        body, size = resolve_body(options.file, options.path)
        visibility = self.resolve_visibility(options.visibility)
        extra: dict[str, Any] = {}
        acl = self._acl(visibility)
        if acl:
            extra["ACL"] = acl
        if options.content_type:
            extra["ContentType"] = options.content_type
        if options.metadata:
            extra["Metadata"] = options.metadata
        if options.content_disposition:
            extra["ContentDisposition"] = options.content_disposition
        if options.content_encoding:
            extra["ContentEncoding"] = options.content_encoding
        if options.cache_control:
            extra["CacheControl"] = options.cache_control
        try:
            self.client.put_object(Bucket=self.bucket, Key=options.key, Body=body, **extra)
        except Exception as exc:
            if _is_acl_unsupported(exc) and "ACL" in extra:
                extra.pop("ACL")
                body, _ = resolve_body(options.file, options.path)
                try:
                    self.client.put_object(Bucket=self.bucket, Key=options.key, Body=body, **extra)
                except Exception as retry_exc:
                    raise _translate(retry_exc, self.provider_name, self.bucket, options.key) from retry_exc
            else:
                raise _translate(exc, self.provider_name, self.bucket, options.key) from exc
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
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise _translate(exc, self.provider_name, self.bucket, key) from exc

    def _exists(self, key: str) -> bool:
        try:
            self._get_metadata(key)
            return True
        except FileNotFoundError:
            return False

    def _get_metadata(self, key: str) -> FileMetadata:
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise _translate(exc, self.provider_name, self.bucket, key) from exc
        return FileMetadata(
            key=key,
            size=int(response.get("ContentLength") or 0),
            content_type=response.get("ContentType") or "application/octet-stream",
            last_modified=response.get("LastModified") or datetime.now(timezone.utc),
            etag=_clean_etag(response.get("ETag")),
            metadata=response.get("Metadata") or None,
        )

    def _list(self, options: Optional[ListOptions] = None) -> ListResult:
        params: dict[str, Any] = {"Bucket": self.bucket}
        if options:
            if options.prefix:
                params["Prefix"] = options.prefix
            if options.limit:
                params["MaxKeys"] = options.limit
            if options.cursor:
                params["ContinuationToken"] = options.cursor
            if options.delimiter:
                params["Delimiter"] = options.delimiter
        try:
            response = self.client.list_objects_v2(**params)
        except Exception as exc:
            raise _translate(exc, self.provider_name, self.bucket) from exc
        files = [
            FileListEntry(
                key=item.get("Key") or "",
                size=int(item.get("Size") or 0),
                last_modified=item.get("LastModified") or datetime.now(timezone.utc),
                etag=_clean_etag(item.get("ETag")),
            )
            for item in response.get("Contents") or []
            if item.get("Key")
        ]
        prefixes = [item.get("Prefix") for item in response.get("CommonPrefixes") or [] if item.get("Prefix")]
        return ListResult(
            files=files,
            next_cursor=response.get("NextContinuationToken"),
            prefixes=prefixes or None,
        )

    def _get_url(self, key: str) -> str:
        encoded = quote(key, safe="/")
        if self.custom_domain:
            return f"{self.custom_domain.rstrip('/')}/{encoded}"
        if self.cdn_url:
            return f"{self.cdn_url.rstrip('/')}/{encoded}"
        if self._public_url_builder:
            return self._public_url_builder(key)
        if self.endpoint:
            if self.force_path_style:
                return f"{self.endpoint}/{self.bucket}/{encoded}"
            return f"{self.endpoint}/{encoded}"
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{encoded}"

    def _get_signed_url(self, key: str, options: Optional[SignedUrlOptions] = None) -> str:
        opts = options or SignedUrlOptions()
        method = opts.method
        client_method = {"GET": "get_object", "PUT": "put_object", "DELETE": "delete_object"}[method]
        params: dict[str, Any] = {"Bucket": self.bucket, "Key": key}
        if method == "PUT" and opts.content_type:
            params["ContentType"] = opts.content_type
        try:
            return self.client.generate_presigned_url(
                ClientMethod=client_method,
                Params=params,
                ExpiresIn=opts.expires_in,
            )
        except Exception as exc:
            raise _translate(exc, self.provider_name, self.bucket, key) from exc

    def _copy(self, options: CopyOptions) -> UploadResult:
        extra: dict[str, Any] = {}
        visibility = self.resolve_visibility(options.visibility)
        acl = self._acl(visibility)
        if acl:
            extra["ACL"] = acl
        if options.metadata or options.content_type:
            extra["MetadataDirective"] = "REPLACE"
            if options.metadata:
                extra["Metadata"] = options.metadata
            if options.content_type:
                extra["ContentType"] = options.content_type
        else:
            extra["MetadataDirective"] = "COPY"
        copy_source = {"Bucket": self.bucket, "Key": options.source_key}
        try:
            self.client.copy_object(
                Bucket=self.bucket,
                Key=options.destination_key,
                CopySource=copy_source,
                **extra,
            )
        except Exception as exc:
            if _is_acl_unsupported(exc) and "ACL" in extra:
                extra.pop("ACL")
                try:
                    self.client.copy_object(
                        Bucket=self.bucket,
                        Key=options.destination_key,
                        CopySource=copy_source,
                        **extra,
                    )
                except Exception as retry_exc:
                    raise _translate(
                        retry_exc, self.provider_name, self.bucket, options.destination_key
                    ) from retry_exc
            else:
                raise _translate(exc, self.provider_name, self.bucket, options.source_key) from exc
        return UploadResult(
            key=options.destination_key,
            url=self._get_url(options.destination_key),
            provider=self.provider_name,
            content_type=options.content_type,
            metadata=options.metadata,
        )

    def _get_stream(self, key: str) -> BinaryIO:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise _translate(exc, self.provider_name, self.bucket, key) from exc
        body = response.get("Body")
        if body is None:
            raise FileNotFoundError(key, self.provider_name)
        if hasattr(body, "read"):
            return body
        return as_bytes_io(bytes(body))

    def _create_multipart_upload(self, key: str, content_type: Optional[str] = None) -> MultipartUploadSession:
        extra: dict[str, Any] = {}
        if content_type:
            extra["ContentType"] = content_type
        try:
            response = self.client.create_multipart_upload(Bucket=self.bucket, Key=key, **extra)
        except Exception as exc:
            raise _translate(exc, self.provider_name, self.bucket, key) from exc
        return MultipartUploadSession(upload_id=response["UploadId"], key=key)

    def _upload_part(self, options: UploadPartOptions) -> MultipartPartResult:
        body, _ = resolve_body(options.body)
        try:
            response = self.client.upload_part(
                Bucket=self.bucket,
                Key=options.key,
                UploadId=options.upload_id,
                PartNumber=options.part_number,
                Body=body,
            )
        except Exception as exc:
            raise _translate(exc, self.provider_name, self.bucket, options.key) from exc
        return MultipartPartResult(
            part_number=options.part_number,
            etag=_clean_etag(response.get("ETag")) or "",
        )

    def _complete_multipart_upload(
        self,
        session: MultipartUploadSession,
        parts: list[MultipartPartResult],
    ) -> UploadResult:
        payload = {
            "Parts": [{"PartNumber": part.part_number, "ETag": part.etag} for part in parts]
        }
        try:
            self.client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=session.key,
                UploadId=session.upload_id,
                MultipartUpload=payload,
            )
        except Exception as exc:
            raise _translate(exc, self.provider_name, self.bucket, session.key) from exc
        return UploadResult(
            key=session.key,
            url=self._get_url(session.key),
            provider=self.provider_name,
        )

    def _abort_multipart_upload(self, session: MultipartUploadSession) -> None:
        try:
            self.client.abort_multipart_upload(
                Bucket=self.bucket,
                Key=session.key,
                UploadId=session.upload_id,
            )
        except Exception as exc:
            raise _translate(exc, self.provider_name, self.bucket, session.key) from exc

    def _list_parts(self, session: MultipartUploadSession) -> list[FileListEntry]:
        try:
            response = self.client.list_parts(
                Bucket=self.bucket,
                Key=session.key,
                UploadId=session.upload_id,
            )
        except Exception as exc:
            raise _translate(exc, self.provider_name, self.bucket, session.key) from exc
        return [
            FileListEntry(
                key=f"{session.key}#part-{item.get('PartNumber') or 0}",
                size=int(item.get("Size") or 0),
                last_modified=item.get("LastModified") or datetime.now(timezone.utc),
                etag=_clean_etag(item.get("ETag")),
            )
            for item in response.get("Parts") or []
        ]


def _clean_etag(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    return value.strip('"')


def _is_acl_unsupported(exc: BaseException) -> bool:
    code = _error_code(exc)
    return code in {"AccessControlListNotSupported", "InvalidRequest", "NotImplemented", "AccessDenied"}


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code") or "")
    return getattr(exc, "response", {}).get("Error", {}).get("Code") if hasattr(exc, "response") else ""


def _translate(exc: BaseException, provider: str, bucket: str, key: Optional[str] = None) -> StoreBridgeLike:
    from py_storebridge.exceptions import StoreBridgeError

    if isinstance(exc, StoreBridgeError):
        return exc
    code = _error_code(exc)
    status = 500
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        status = int(response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 500)
    message = str(exc)
    if code in {"404", "NoSuchKey", "NotFound", "NoSuchUpload"} or status == 404:
        if key:
            return FileNotFoundError(key, provider, exc)
        return ProviderError(message, provider, exc, 404)
    if code in {"403", "AccessDenied", "AllAccessDisabled"} or status == 403:
        return PermissionError(message, provider, exc)
    if code in {"NoSuchBucket", "NoSuchBucketPolicy"}:
        return BucketNotFoundError(bucket, provider, exc)
    if code in {"RequestTimeout", "SlowDown"} or status in {408, 429, 500, 502, 503, 504}:
        return NetworkError(message, provider, exc)
    return ProviderError(message, provider, exc, status)


# For type checkers; the function always returns a StoreBridgeError subclass.
StoreBridgeLike = Any
