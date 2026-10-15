"""Pydantic models for storage operations and results."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from py_storebridge.config import Visibility
from py_storebridge.exceptions import ValidationError
from py_storebridge.keys import validate_key


class _Model(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
        arbitrary_types_allowed=True,
        str_strip_whitespace=True,
        use_enum_values=True,
    )


class UploadOptions(_Model):
    key: str = Field(min_length=1)
    file: Any = None
    path: Optional[Union[str, Path]] = None
    content_type: Optional[str] = Field(default=None, alias="contentType")
    visibility: Optional[Visibility] = None
    metadata: Optional[Dict[str, str]] = None
    chunk_size: Optional[int] = Field(default=None, ge=1, alias="chunkSize")
    multipart: Optional[bool] = None
    content_disposition: Optional[str] = Field(default=None, alias="contentDisposition")
    content_encoding: Optional[str] = Field(default=None, alias="contentEncoding")
    cache_control: Optional[str] = Field(default=None, alias="cacheControl")
    on_progress: Optional[Callable[[int, int], None]] = Field(default=None, alias="onProgress")

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return validate_key(value)

    @model_validator(mode="after")
    def _body(self) -> "UploadOptions":
        if self.file is None and self.path is None:
            raise ValueError("Provide file or path")
        if self.metadata:
            for name, value in self.metadata.items():
                if not isinstance(name, str) or not isinstance(value, str):
                    raise ValueError("metadata keys and values must be strings")
        return self


class UploadResult(_Model):
    key: str
    url: str
    provider: str
    etag: Optional[str] = None
    size: Optional[int] = None
    content_type: Optional[str] = Field(default=None, alias="contentType")
    metadata: Optional[Dict[str, str]] = None


class DeleteOptions(_Model):
    key: str = Field(min_length=1)

    @field_validator("key")
    @classmethod
    def _key(cls, value: str) -> str:
        return validate_key(value)


class ListOptions(_Model):
    prefix: Optional[str] = None
    limit: Optional[int] = Field(default=None, ge=1, le=10_000)
    cursor: Optional[str] = None
    delimiter: Optional[str] = None


class FileListEntry(_Model):
    key: str
    size: int = 0
    last_modified: datetime = Field(default_factory=lambda: datetime.now().astimezone(), alias="lastModified")
    etag: Optional[str] = None


class ListResult(_Model):
    files: List[FileListEntry] = Field(default_factory=list)
    next_cursor: Optional[str] = Field(default=None, alias="nextCursor")
    prefixes: Optional[List[str]] = None


class FileMetadata(_Model):
    key: str
    size: int = 0
    content_type: str = Field(default="application/octet-stream", alias="contentType")
    last_modified: datetime = Field(default_factory=lambda: datetime.now().astimezone(), alias="lastModified")
    etag: Optional[str] = None
    metadata: Optional[Dict[str, str]] = None
    visibility: Optional[Visibility] = None


class SignedUrlOptions(_Model):
    expires_in: int = Field(default=3600, ge=1, le=7 * 24 * 3600, alias="expiresIn")
    method: Literal["GET", "PUT", "DELETE"] = "GET"
    content_type: Optional[str] = Field(default=None, alias="contentType")
    query_params: Optional[Dict[str, str]] = Field(default=None, alias="queryParams")


class CopyOptions(_Model):
    source_key: str = Field(min_length=1, alias="sourceKey")
    destination_key: str = Field(min_length=1, alias="destinationKey")
    visibility: Optional[Visibility] = None
    metadata: Optional[Dict[str, str]] = None
    content_type: Optional[str] = Field(default=None, alias="contentType")

    @field_validator("source_key", "destination_key")
    @classmethod
    def _key(cls, value: str) -> str:
        return validate_key(value)


class MultipartUploadSession(_Model):
    upload_id: str = Field(alias="uploadId")
    key: str


class MultipartPartResult(_Model):
    part_number: int = Field(ge=1, alias="partNumber")
    etag: str


class UploadPartOptions(_Model):
    upload_id: str = Field(min_length=1, alias="uploadId")
    key: str = Field(min_length=1)
    part_number: int = Field(ge=1, alias="partNumber")
    body: Any


class TransferItemStatus(str, Enum):
    TRANSFERRED = "transferred"
    SKIPPED = "skipped"
    FAILED = "failed"
    PLANNED = "planned"


class TransferItemResult(_Model):
    source_key: str = Field(alias="sourceKey")
    destination_key: str = Field(alias="destinationKey")
    status: TransferItemStatus
    size: int = 0
    error: Optional[str] = None
    url: Optional[str] = None


class TransferOptions(_Model):
    """Copy or move objects from one provider to another."""

    source_key: Optional[str] = Field(default=None, alias="sourceKey")
    keys: Optional[List[str]] = None
    prefix: Optional[str] = None
    destination_key: Optional[str] = Field(default=None, alias="destinationKey")
    destination_prefix: Optional[str] = Field(default=None, alias="destinationPrefix")
    delete_source: bool = Field(default=False, alias="deleteSource")
    overwrite: bool = True
    skip_existing: bool = Field(default=False, alias="skipExisting")
    concurrency: int = Field(default=8, ge=1, le=64)
    preserve_metadata: bool = Field(default=True, alias="preserveMetadata")
    preserve_visibility: bool = Field(default=True, alias="preserveVisibility")
    dry_run: bool = Field(default=False, alias="dryRun")
    continue_on_error: bool = Field(default=True, alias="continueOnError")
    on_progress: Optional[Callable[[int, int, str], None]] = Field(default=None, alias="onProgress")
    on_item: Optional[Callable[[TransferItemResult], None]] = Field(default=None, alias="onItem")

    @model_validator(mode="after")
    def _scope(self) -> "TransferOptions":
        selected = [self.source_key, self.prefix, self.keys]
        filled = sum(1 for item in selected if item)
        if filled > 1:
            raise ValueError("Provide only one of source_key, prefix, or keys")
        if self.source_key:
            validate_key(self.source_key)
        if self.destination_key:
            validate_key(self.destination_key)
        if self.keys:
            for key in self.keys:
                validate_key(key)
        if self.destination_key and not self.source_key:
            raise ValueError("destination_key can only be used with source_key")
        return self


class TransferResult(_Model):
    transferred: int = 0
    skipped: int = 0
    failed: int = 0
    planned: int = 0
    bytes_transferred: int = Field(default=0, alias="bytesTransferred")
    items: List[TransferItemResult] = Field(default_factory=list)
    source_provider: str = Field(alias="sourceProvider")
    destination_provider: str = Field(alias="destinationProvider")
    dry_run: bool = Field(default=False, alias="dryRun")

    @property
    def ok(self) -> bool:
        return self.failed == 0


def parse_model(model_cls: type[BaseModel], data: Any, **kwargs: Any) -> Any:
    payload = data if data is not None else kwargs
    if isinstance(payload, model_cls):
        return payload
    if isinstance(payload, BaseModel):
        payload = payload.model_dump()
    try:
        return model_cls.model_validate(payload)
    except Exception as exc:
        message = str(exc)
        raise ValidationError(message) from exc
