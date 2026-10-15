from __future__ import annotations

from typing import Optional

from py_storebridge.config import LogLevel, RetryConfig, S3Credentials, Visibility
from py_storebridge.providers.s3compat import S3CompatibleProvider


class S3Provider(S3CompatibleProvider):
    def __init__(
        self,
        credentials: S3Credentials,
        *,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
        client=None,
    ) -> None:
        force_path = credentials.force_path_style
        if force_path is None:
            force_path = bool(credentials.endpoint)
        super().__init__(
            provider_name="s3",
            extra_name="s3",
            bucket=credentials.bucket,
            region=credentials.region,
            access_key_id=credentials.access_key_id,
            secret_access_key=credentials.secret_access_key,
            endpoint=credentials.endpoint,
            force_path_style=force_path,
            session_token=credentials.session_token,
            cdn_url=credentials.cdn_url,
            custom_domain=credentials.custom_domain,
            supports_acl=True,
            retry=retry,
            log_level=log_level,
            default_visibility=default_visibility,
            client=client,
        )
