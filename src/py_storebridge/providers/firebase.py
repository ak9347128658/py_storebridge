from __future__ import annotations

from typing import Optional

from py_storebridge.config import FirebaseCredentials, GCSCredentials, GCSServiceAccount, LogLevel, RetryConfig, Visibility
from py_storebridge.providers.gcs import GCSProvider


class FirebaseProvider(GCSProvider):
    """Firebase Storage is a GCS bucket with Firebase project credentials."""

    provider_name = "firebase"
    extra_name = "firebase"

    def __init__(
        self,
        credentials: FirebaseCredentials,
        *,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
        client=None,
    ) -> None:
        nested = None
        account = credentials.service_account or {}
        if account.get("client_email") and account.get("private_key"):
            nested = GCSServiceAccount(
                client_email=str(account["client_email"]),
                private_key=str(account["private_key"]),
            )
        gcs = GCSCredentials(
            project_id=credentials.project_id,
            bucket=credentials.bucket,
            key_filename=credentials.key_filename,
            credentials=nested,
            service_account=credentials.service_account,
            cdn_url=credentials.cdn_url,
            custom_domain=credentials.custom_domain,
        )
        super().__init__(
            gcs,
            retry=retry,
            log_level=log_level,
            default_visibility=default_visibility,
            client=client,
        )
        self.provider_name = "firebase"
        self.extra_name = "firebase"
