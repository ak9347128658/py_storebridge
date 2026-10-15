from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from py_storebridge.config import B2Credentials, LogLevel, RetryConfig, Visibility
from py_storebridge.providers.s3compat import S3CompatibleProvider


class B2Provider(S3CompatibleProvider):
    def __init__(
        self,
        credentials: B2Credentials,
        *,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
        client=None,
    ) -> None:
        region = credentials.region or "us-west-004"
        endpoint = credentials.endpoint or f"https://s3.{region}.backblazeb2.com"

        def builder(key: str) -> str:
            return f"{endpoint}/{credentials.bucket}/{quote(key, safe='/')}"

        super().__init__(
            provider_name="b2",
            extra_name="b2",
            bucket=credentials.bucket,
            region=region,
            access_key_id=credentials.application_key_id,
            secret_access_key=credentials.application_key,
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
