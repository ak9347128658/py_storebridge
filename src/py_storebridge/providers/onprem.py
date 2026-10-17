"""On-premises provider.

``engine='auto'`` stores on the local disk when no service is configured.
Point ``endpoint`` at MinIO, Ceph RGW, Garage, or SeaweedFS to use that
service. ``install=true`` starts MinIO on this machine when the endpoint is
down. See ``onprem_install`` for the full decision order.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, BinaryIO, List, Optional
from urllib.parse import quote

from py_storebridge.config import LogLevel, OnPremCredentials, RetryConfig, Visibility
from py_storebridge.exceptions import ProviderError
from py_storebridge.providers.base import BaseProvider
from py_storebridge.providers.onprem_fs import FilesystemBackend, load_signing_secret
from py_storebridge.providers.onprem_install import OnPremPlan, resolve_onprem
from py_storebridge.schemas import (
    CopyOptions,
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

_VISIBILITY_META = "storebridge-visibility"


def _visibility_text(value: Any) -> str:
    return str(value.value if hasattr(value, "value") else value)


def _public_metadata(metadata: Optional[dict]) -> Optional[dict]:
    if not metadata:
        return None
    cleaned = {key: val for key, val in metadata.items() if key.lower() != _VISIBILITY_META}
    return cleaned or None


class _OnPremS3Backend:
    """S3-compatible on-prem store with visibility stored in object metadata."""

    def __init__(self, provider: BaseProvider) -> None:
        self._provider = provider

    def _stamp(self, options: UploadOptions) -> UploadOptions:
        visibility = _visibility_text(self._provider.resolve_visibility(options.visibility))
        metadata = dict(options.metadata or {})
        metadata[_VISIBILITY_META] = visibility
        return options.model_copy(update={"metadata": metadata})

    def _upload(self, options: UploadOptions) -> UploadResult:
        stamped = self._stamp(options)
        result = self._provider._upload(stamped)
        return result.model_copy(update={"metadata": _public_metadata(result.metadata)})

    def _delete(self, key: str) -> None:
        self._provider._delete(key)

    def _exists(self, key: str) -> bool:
        return self._provider._exists(key)

    def _get_metadata(self, key: str) -> FileMetadata:
        meta = self._provider._get_metadata(key)
        raw = dict(meta.metadata or {})
        visibility = None
        for name in list(raw):
            if name.lower() == _VISIBILITY_META:
                visibility = raw.pop(name)
        return meta.model_copy(update={"metadata": raw or None, "visibility": visibility})

    def _list(self, options: Optional[ListOptions] = None) -> ListResult:
        return self._provider._list(options)

    def _get_url(self, key: str) -> str:
        return self._provider._get_url(key)

    def _get_signed_url(self, key: str, options: Optional[SignedUrlOptions] = None) -> str:
        return self._provider._get_signed_url(key, options)

    def _copy(self, options: CopyOptions) -> UploadResult:
        source = self._get_metadata(options.source_key)
        visibility = options.visibility or source.visibility or self._provider.default_visibility
        metadata = dict(options.metadata or source.metadata or {})
        metadata[_VISIBILITY_META] = _visibility_text(self._provider.resolve_visibility(visibility))
        copied = options.model_copy(
            update={
                "metadata": metadata,
                "content_type": options.content_type or source.content_type,
            }
        )
        result = self._provider._copy(copied)
        return result.model_copy(update={"metadata": _public_metadata(copied.metadata)})

    def _get_stream(self, key: str) -> BinaryIO:
        return self._provider._get_stream(key)

    def _create_multipart_upload(self, key: str, content_type: Optional[str] = None) -> MultipartUploadSession:
        return self._provider._create_multipart_upload(key, content_type)

    def _upload_part(self, options: UploadPartOptions) -> MultipartPartResult:
        return self._provider._upload_part(options)

    def _complete_multipart_upload(self, session: MultipartUploadSession, parts: List[MultipartPartResult]) -> UploadResult:
        return self._provider._complete_multipart_upload(session, parts)

    def _abort_multipart_upload(self, session: MultipartUploadSession) -> None:
        self._provider._abort_multipart_upload(session)

    def _list_parts(self, session: MultipartUploadSession) -> list:
        return self._provider._list_parts(session)


class OnPremProvider(BaseProvider):
    provider_name = "onprem"

    def __init__(
        self,
        credentials: OnPremCredentials,
        *,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
        client: Any = None,
        probe=None,
        installer=None,
    ) -> None:
        super().__init__(retry=retry, log_level=log_level, default_visibility=default_visibility)
        kwargs = {}
        if probe is not None:
            kwargs["probe"] = probe
        if installer is not None:
            kwargs["installer"] = installer
        self.plan: OnPremPlan = resolve_onprem(credentials, **kwargs)
        self.root = self.plan.root
        self.engine = self.plan.engine
        self.endpoint = self.plan.endpoint
        self._server = None
        self.signing_secret: Optional[str] = None
        if self.plan.engine == "filesystem":
            root = Path(self.plan.root)
            self.signing_secret = load_signing_secret(root, credentials.signing_secret)
            serve_port = 8787 if credentials.serve_port is None else credentials.serve_port
            base_url = credentials.base_url or f"http://{_public_host(credentials.host)}:{serve_port}"
            self._backend = FilesystemBackend(
                root=root,
                bucket=credentials.bucket,
                signing_secret=self.signing_secret,
                base_url=base_url,
                custom_domain=credentials.custom_domain,
                cdn_url=credentials.cdn_url,
                retry=retry,
                log_level=log_level,
                default_visibility=default_visibility,
                max_upload_bytes=credentials.max_upload_bytes,
            )
            if credentials.serve:
                from py_storebridge.providers.onprem_server import OnPremServer

                self._server = OnPremServer(
                    self._backend,
                    credentials.host,
                    serve_port,
                    cors_origin=credentials.cors_origin,
                )
                served = self._server.start()
                if not credentials.base_url and not credentials.custom_domain and not credentials.cdn_url:
                    self._backend.base_url = served
            self.logger.info("onprem:filesystem", root=str(root), detail=self.plan.detail)
        else:
            self._backend = self._build_s3(credentials, client)
            log = self.logger.warn if self.plan.installed else self.logger.info
            log("onprem:s3", endpoint=self.plan.endpoint, detail=self.plan.detail)

    def _build_s3(self, credentials: OnPremCredentials, client: Any) -> _OnPremS3Backend:
        from py_storebridge.providers.s3compat import S3CompatibleProvider

        if not self.plan.endpoint or not self.plan.access_key or not self.plan.secret_key:
            raise ProviderError("On-premises S3 engine is missing endpoint or credentials.", "onprem")

        def builder(key: str) -> str:
            return f"{self.plan.endpoint}/{credentials.bucket}/{quote(key, safe='/')}"

        raw = S3CompatibleProvider(
            provider_name="onprem",
            extra_name="onprem",
            bucket=credentials.bucket,
            region=self.plan.region,
            access_key_id=self.plan.access_key,
            secret_access_key=self.plan.secret_key,
            endpoint=self.plan.endpoint,
            force_path_style=credentials.force_path_style,
            cdn_url=credentials.cdn_url,
            custom_domain=credentials.custom_domain,
            supports_acl=credentials.supports_acl,
            public_url_builder=builder,
            retry=self.retry_config,
            log_level=None,
            default_visibility=self.default_visibility,
            client=client,
        )
        if credentials.create_bucket:
            _ensure_bucket(raw)
        return _OnPremS3Backend(raw)

    def close(self) -> None:
        server = self._server
        self._server = None
        if server is not None:
            server.stop()

    def _upload(self, options: UploadOptions) -> UploadResult:
        return self._backend._upload(options)

    def _delete(self, key: str) -> None:
        self._backend._delete(key)

    def _exists(self, key: str) -> bool:
        return self._backend._exists(key)

    def _get_metadata(self, key: str) -> FileMetadata:
        return self._backend._get_metadata(key)

    def _list(self, options: Optional[ListOptions] = None) -> ListResult:
        return self._backend._list(options)

    def _get_url(self, key: str) -> str:
        return self._backend._get_url(key)

    def _get_signed_url(self, key: str, options: Optional[SignedUrlOptions] = None) -> str:
        return self._backend._get_signed_url(key, options)

    def _copy(self, options: CopyOptions) -> UploadResult:
        return self._backend._copy(options)

    def _get_stream(self, key: str) -> BinaryIO:
        return self._backend._get_stream(key)

    def _create_multipart_upload(self, key: str, content_type: Optional[str] = None) -> MultipartUploadSession:
        return self._backend._create_multipart_upload(key, content_type)

    def _upload_part(self, options: UploadPartOptions) -> MultipartPartResult:
        return self._backend._upload_part(options)

    def _complete_multipart_upload(
        self,
        session: MultipartUploadSession,
        parts: List[MultipartPartResult],
    ) -> UploadResult:
        return self._backend._complete_multipart_upload(session, parts)

    def _abort_multipart_upload(self, session: MultipartUploadSession) -> None:
        self._backend._abort_multipart_upload(session)

    def _list_parts(self, session: MultipartUploadSession) -> list:
        return self._backend._list_parts(session)


def _public_host(host: str) -> str:
    if host in {"0.0.0.0", "::"}:
        return "127.0.0.1"
    return host


def _ensure_bucket(provider) -> None:
    """Create the bucket. Retry while a service we just started is still booting."""
    for attempt in range(5):
        try:
            provider.client.head_bucket(Bucket=provider.bucket)
            return
        except Exception as exc:
            code = _error_code(exc)
            status = _status_code(exc)
            if code in {"404", "NoSuchBucket", "NotFound"} or status == 404:
                break
            if attempt == 4 or not _retryable_startup_error(exc, code, status):
                break
            time.sleep(0.4)
    try:
        provider.client.create_bucket(Bucket=provider.bucket)
    except Exception as exc:
        code = _error_code(exc)
        if code in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            return
        raise ProviderError(
            f"Could not create on-premises bucket '{provider.bucket}': {exc}",
            "onprem",
            exc,
        ) from exc


def _error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        return str(response.get("Error", {}).get("Code") or "")
    return ""


def _retryable_startup_error(exc: BaseException, code: str, status: int) -> bool:
    if status in {408, 429, 500, 502, 503, 504} or code in {"RequestTimeout", "SlowDown"}:
        return True
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True
    return type(exc).__name__ in {
        "EndpointConnectionError",
        "ConnectionClosedError",
        "ConnectTimeoutError",
        "ReadTimeoutError",
    }


def _status_code(exc: BaseException) -> int:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        try:
            return int(response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0)
        except (TypeError, ValueError):
            return 0
    return 0
