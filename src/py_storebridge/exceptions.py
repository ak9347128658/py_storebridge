"""Public exception hierarchy for py_storebridge."""

from __future__ import annotations

from typing import Any, Optional


class StoreBridgeError(Exception):
    """Base error for every py_storebridge failure."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "STOREBRIDGE_ERROR",
        status_code: int = 500,
        provider: Optional[str] = None,
        original_error: Optional[BaseException] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.provider = provider
        self.original_error = original_error

    def __str__(self) -> str:
        prefix = f"[{self.provider}] " if self.provider else ""
        return f"{prefix}{self.message}"


# Alias kept for readers coming from the Node.js storebridge package.
StorixError = StoreBridgeError


class ProviderError(StoreBridgeError):
    def __init__(
        self,
        message: str,
        provider: str,
        original_error: Optional[BaseException] = None,
        status_code: int = 500,
    ) -> None:
        super().__init__(
            message,
            code="PROVIDER_ERROR",
            status_code=status_code,
            provider=provider,
            original_error=original_error,
        )


class FileNotFoundError(StoreBridgeError):
    """Raised when a storage object does not exist."""

    def __init__(
        self,
        key: str,
        provider: str,
        original_error: Optional[BaseException] = None,
    ) -> None:
        super().__init__(
            f'File not found: "{key}"',
            code="FILE_NOT_FOUND",
            status_code=404,
            provider=provider,
            original_error=original_error,
        )
        self.key = key


class PermissionError(StoreBridgeError):
    def __init__(
        self,
        message: str,
        provider: str,
        original_error: Optional[BaseException] = None,
    ) -> None:
        super().__init__(
            message,
            code="PERMISSION_DENIED",
            status_code=403,
            provider=provider,
            original_error=original_error,
        )


class BucketNotFoundError(StoreBridgeError):
    def __init__(
        self,
        bucket: str,
        provider: str,
        original_error: Optional[BaseException] = None,
    ) -> None:
        super().__init__(
            f'Bucket not found: "{bucket}"',
            code="BUCKET_NOT_FOUND",
            status_code=404,
            provider=provider,
            original_error=original_error,
        )
        self.bucket = bucket


class MultipartUploadError(StoreBridgeError):
    def __init__(
        self,
        message: str,
        provider: str,
        original_error: Optional[BaseException] = None,
    ) -> None:
        super().__init__(
            message,
            code="MULTIPART_UPLOAD_ERROR",
            status_code=500,
            provider=provider,
            original_error=original_error,
        )


class NetworkError(StoreBridgeError):
    def __init__(
        self,
        message: str,
        provider: str,
        original_error: Optional[BaseException] = None,
    ) -> None:
        super().__init__(
            message,
            code="NETWORK_ERROR",
            status_code=503,
            provider=provider,
            original_error=original_error,
        )


class MaxRetriesExceededError(StoreBridgeError):
    def __init__(
        self,
        attempts: int,
        provider: str,
        original_error: Optional[BaseException] = None,
    ) -> None:
        super().__init__(
            f"Max retries exceeded after {attempts} attempt(s)",
            code="MAX_RETRIES_EXCEEDED",
            status_code=503,
            provider=provider,
            original_error=original_error,
        )
        self.attempts = attempts


class ValidationError(StoreBridgeError):
    def __init__(self, message: str, field: Optional[str] = None) -> None:
        super().__init__(message, code="VALIDATION_ERROR", status_code=400)
        self.field = field


class MissingConfigError(ValidationError):
    def __init__(self, field: str, provider: str) -> None:
        super().__init__(
            f'Missing required configuration field "{field}" for provider "{provider}"',
            field=field,
        )
        self.provider = provider


class UnknownMimeTypeError(StoreBridgeError):
    def __init__(self, key: str) -> None:
        super().__init__(
            f'Cannot determine MIME type for key: "{key}"',
            code="UNKNOWN_MIME_TYPE",
            status_code=400,
        )
        self.key = key


class MissingDependencyError(StoreBridgeError):
    def __init__(self, package: str, extra: str) -> None:
        super().__init__(
            (
                f"The '{package}' package is required for this provider. "
                f"Install it with: pip install py_storebridge[{extra}]"
            ),
            code="MISSING_DEPENDENCY",
            status_code=500,
        )
        self.package = package
        self.extra = extra


class TransferError(StoreBridgeError):
    """Raised when a cross-provider transfer stops because of a failed item."""

    def __init__(
        self,
        message: str,
        *,
        result: Any = None,
        original_error: Optional[BaseException] = None,
    ) -> None:
        super().__init__(
            message,
            code="TRANSFER_ERROR",
            status_code=500,
            original_error=original_error,
        )
        self.result = result
