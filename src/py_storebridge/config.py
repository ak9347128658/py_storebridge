"""Pydantic configuration and credential models."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Dict, List, Literal, Optional, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError as PydanticValidationError,
    field_validator,
    model_validator,
)

from py_storebridge.exceptions import ValidationError


class Visibility(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"


class LogLevel(str, Enum):
    DEBUG = "debug"
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    SILENT = "silent"


class _Model(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        str_strip_whitespace=True,
        use_enum_values=True,
    )


class RetryConfig(_Model):
    """Retry policy. Delays are milliseconds, matching the Node.js SDK."""

    max_retries: int = Field(default=3, ge=0, le=50, alias="maxRetries")
    base_delay: int = Field(default=200, ge=0, alias="baseDelay")
    max_delay: int = Field(default=10_000, ge=0, alias="maxDelay")
    retryable_status_codes: List[int] = Field(
        default_factory=lambda: [408, 429, 500, 502, 503, 504],
        alias="retryableStatusCodes",
    )

    @model_validator(mode="after")
    def _delays(self) -> "RetryConfig":
        if self.max_delay < self.base_delay:
            raise ValueError("max_delay must be greater than or equal to base_delay")
        return self


class S3Credentials(_Model):
    access_key_id: str = Field(min_length=1, alias="accessKeyId")
    secret_access_key: str = Field(min_length=1, alias="secretAccessKey")
    region: str = Field(min_length=1)
    bucket: str = Field(min_length=1)
    endpoint: Optional[str] = None
    force_path_style: Optional[bool] = Field(default=None, alias="forcePathStyle")
    session_token: Optional[str] = Field(default=None, alias="sessionToken")
    cdn_url: Optional[str] = Field(default=None, alias="cdnUrl")
    custom_domain: Optional[str] = Field(default=None, alias="customDomain")


class R2Credentials(_Model):
    account_id: str = Field(min_length=1, alias="accountId")
    access_key_id: str = Field(min_length=1, alias="accessKeyId")
    secret_access_key: str = Field(min_length=1, alias="secretAccessKey")
    bucket: str = Field(min_length=1)
    cdn_url: Optional[str] = Field(default=None, alias="cdnUrl")
    custom_domain: Optional[str] = Field(default=None, alias="customDomain")


class GCSServiceAccount(_Model):
    model_config = ConfigDict(populate_by_name=True, extra="allow")

    client_email: str = Field(min_length=1)
    private_key: str = Field(min_length=1)


class GCSCredentials(_Model):
    project_id: str = Field(min_length=1, alias="projectId")
    bucket: str = Field(min_length=1)
    key_filename: Optional[str] = Field(default=None, alias="keyFilename")
    credentials: Optional[GCSServiceAccount] = None
    service_account: Optional[Dict[str, Any]] = Field(default=None, alias="serviceAccount")
    cdn_url: Optional[str] = Field(default=None, alias="cdnUrl")
    custom_domain: Optional[str] = Field(default=None, alias="customDomain")

    @field_validator("credentials", mode="before")
    @classmethod
    def _coerce_credentials(cls, value: Any) -> Any:
        if isinstance(value, dict) and "client_email" in value:
            return value
        return value


class AzureCredentials(_Model):
    account_name: str = Field(min_length=1, alias="accountName")
    container_name: str = Field(min_length=1, alias="containerName")
    account_key: Optional[str] = Field(default=None, alias="accountKey")
    connection_string: Optional[str] = Field(default=None, alias="connectionString")
    cdn_url: Optional[str] = Field(default=None, alias="cdnUrl")
    custom_domain: Optional[str] = Field(default=None, alias="customDomain")

    @model_validator(mode="after")
    def _auth(self) -> "AzureCredentials":
        if not self.account_key and not self.connection_string:
            raise ValueError("Provide account_key or connection_string")
        return self


class SpacesCredentials(_Model):
    access_key_id: str = Field(min_length=1, alias="accessKeyId")
    secret_access_key: str = Field(min_length=1, alias="secretAccessKey")
    region: str = Field(min_length=1)
    bucket: str = Field(min_length=1)
    endpoint: Optional[str] = None
    cdn_url: Optional[str] = Field(default=None, alias="cdnUrl")
    custom_domain: Optional[str] = Field(default=None, alias="customDomain")


class B2Credentials(_Model):
    application_key_id: str = Field(min_length=1, alias="applicationKeyId")
    application_key: str = Field(min_length=1, alias="applicationKey")
    bucket: str = Field(min_length=1)
    endpoint: Optional[str] = None
    region: Optional[str] = "us-west-004"
    cdn_url: Optional[str] = Field(default=None, alias="cdnUrl")
    custom_domain: Optional[str] = Field(default=None, alias="customDomain")


class MinIOCredentials(_Model):
    end_point: str = Field(min_length=1, alias="endPoint")
    access_key: str = Field(min_length=1, alias="accessKey")
    secret_key: str = Field(min_length=1, alias="secretKey")
    bucket: str = Field(min_length=1)
    port: Optional[int] = Field(default=9000, ge=1, le=65535)
    use_ssl: bool = Field(default=True, alias="useSSL")
    region: Optional[str] = "us-east-1"
    cdn_url: Optional[str] = Field(default=None, alias="cdnUrl")
    custom_domain: Optional[str] = Field(default=None, alias="customDomain")


class SupabaseCredentials(_Model):
    url: str = Field(min_length=1)
    service_role_key: str = Field(min_length=1, alias="serviceRoleKey")
    bucket: str = Field(min_length=1)
    cdn_url: Optional[str] = Field(default=None, alias="cdnUrl")
    custom_domain: Optional[str] = Field(default=None, alias="customDomain")

    @field_validator("url")
    @classmethod
    def _url(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return value.rstrip("/")


class FirebaseCredentials(_Model):
    project_id: str = Field(min_length=1, alias="projectId")
    bucket: str = Field(min_length=1)
    service_account: Optional[Dict[str, Any]] = Field(default=None, alias="serviceAccount")
    key_filename: Optional[str] = Field(default=None, alias="keyFilename")
    cdn_url: Optional[str] = Field(default=None, alias="cdnUrl")
    custom_domain: Optional[str] = Field(default=None, alias="customDomain")


class AppwriteCredentials(_Model):
    endpoint: str = Field(min_length=1)
    project_id: str = Field(min_length=1, alias="projectId")
    api_key: str = Field(min_length=1, alias="apiKey")
    bucket_id: str = Field(min_length=1, alias="bucketId")
    cdn_url: Optional[str] = Field(default=None, alias="cdnUrl")
    custom_domain: Optional[str] = Field(default=None, alias="customDomain")

    @field_validator("endpoint")
    @classmethod
    def _endpoint(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("endpoint must start with http:// or https://")
        return value.rstrip("/")


class _Shared(_Model):
    visibility: Visibility = Visibility.PRIVATE
    retry: RetryConfig = Field(default_factory=RetryConfig)
    log_level: LogLevel = Field(default=LogLevel.WARN, alias="logLevel")


class S3Config(_Shared):
    provider: Literal["s3"] = "s3"
    credentials: S3Credentials


class R2Config(_Shared):
    provider: Literal["r2"] = "r2"
    credentials: R2Credentials


class GCSConfig(_Shared):
    provider: Literal["gcs"] = "gcs"
    credentials: GCSCredentials


class AzureConfig(_Shared):
    provider: Literal["azure"] = "azure"
    credentials: AzureCredentials


class SpacesConfig(_Shared):
    provider: Literal["spaces"] = "spaces"
    credentials: SpacesCredentials


class B2Config(_Shared):
    provider: Literal["b2"] = "b2"
    credentials: B2Credentials


class MinIOConfig(_Shared):
    provider: Literal["minio"] = "minio"
    credentials: MinIOCredentials


class SupabaseConfig(_Shared):
    provider: Literal["supabase"] = "supabase"
    credentials: SupabaseCredentials


class FirebaseConfig(_Shared):
    provider: Literal["firebase"] = "firebase"
    credentials: FirebaseCredentials


class AppwriteConfig(_Shared):
    provider: Literal["appwrite"] = "appwrite"
    credentials: AppwriteCredentials


StorageConfig = Annotated[
    Union[
        S3Config,
        R2Config,
        GCSConfig,
        AzureConfig,
        SpacesConfig,
        B2Config,
        MinIOConfig,
        SupabaseConfig,
        FirebaseConfig,
        AppwriteConfig,
    ],
    Field(discriminator="provider"),
]

_STORAGE_CONFIG_ADAPTER: TypeAdapter[StorageConfig] = TypeAdapter(StorageConfig)

PROVIDERS = (
    "s3",
    "r2",
    "gcs",
    "azure",
    "spaces",
    "b2",
    "minio",
    "supabase",
    "firebase",
    "appwrite",
)


def parse_storage_config(data: Any) -> StorageConfig:
    """Validate a dict or model into a discriminated StorageConfig."""
    if isinstance(data, _Shared) and hasattr(data, "provider"):
        return data  # type: ignore[return-value]
    try:
        return _STORAGE_CONFIG_ADAPTER.validate_python(data)
    except PydanticValidationError as exc:
        raise ValidationError(_format_pydantic(exc)) from exc


def _format_pydantic(exc: PydanticValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")))
    return "Invalid storage configuration: " + "; ".join(parts)
