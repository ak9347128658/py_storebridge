from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from py_storebridge.config import LogLevel, MinIOCredentials, RetryConfig, Visibility
from py_storebridge.providers.s3compat import S3CompatibleProvider


class MinIOProvider(S3CompatibleProvider):
    def __init__(
        self,
        credentials: MinIOCredentials,
        *,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
        client=None,
    ) -> None:
        scheme = "https" if credentials.use_ssl else "http"
        host = credentials.end_point
        port = credentials.port or (443 if credentials.use_ssl else 80)
        default_port = (credentials.use_ssl and port == 443) or (not credentials.use_ssl and port == 80)
        endpoint = f"{scheme}://{host}" if default_port else f"{scheme}://{host}:{port}"

        def builder(key: str) -> str:
            return f"{endpoint}/{credentials.bucket}/{quote(key, safe='/')}"

        super().__init__(
            provider_name="minio",
            extra_name="minio",
            bucket=credentials.bucket,
            region=credentials.region or "us-east-1",
            access_key_id=credentials.access_key,
            secret_access_key=credentials.secret_key,
            endpoint=endpoint,
            force_path_style=True,
            cdn_url=credentials.cdn_url,
            custom_domain=credentials.custom_domain,
            supports_acl=False,
            public_url_builder=builder,
            retry=retry,
            log_level=log_level,
            default_visibility=default_visibility,
            client=client,
        )
