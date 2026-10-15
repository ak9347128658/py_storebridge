from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from py_storebridge.config import LogLevel, RetryConfig, SpacesCredentials, Visibility
from py_storebridge.providers.s3compat import S3CompatibleProvider


class SpacesProvider(S3CompatibleProvider):
    def __init__(
        self,
        credentials: SpacesCredentials,
        *,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
        client=None,
    ) -> None:
        endpoint = credentials.endpoint or f"https://{credentials.region}.digitaloceanspaces.com"

        def builder(key: str) -> str:
            return f"https://{credentials.bucket}.{credentials.region}.digitaloceanspaces.com/{quote(key, safe='/')}"

        super().__init__(
            provider_name="spaces",
            extra_name="spaces",
            bucket=credentials.bucket,
            region=credentials.region,
            access_key_id=credentials.access_key_id,
            secret_access_key=credentials.secret_access_key,
            endpoint=endpoint,
            force_path_style=False,
            cdn_url=credentials.cdn_url,
            custom_domain=credentials.custom_domain,
            supports_acl=True,
            public_url_builder=builder,
            retry=retry,
            log_level=log_level,
            default_visibility=default_visibility,
            client=client,
        )
