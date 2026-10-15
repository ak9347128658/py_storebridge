"""Helpers for turning upload bodies into bytes or file objects."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any, BinaryIO, Optional, Tuple, Union

from py_storebridge.exceptions import ValidationError

FileBody = Union[bytes, bytearray, memoryview, str, Path, BinaryIO]


def resolve_body(
    file: Any = None,
    path: Optional[Union[str, Path]] = None,
) -> Tuple[Union[bytes, BinaryIO], Optional[int]]:
    """Return ``(body, size_or_none)`` suitable for provider uploads."""
    if path is not None:
        file_path = Path(path)
        if not file_path.is_file():
            raise ValidationError(f"Upload path does not exist: {file_path}", field="path")
        handle = file_path.open("rb")
        return handle, file_path.stat().st_size

    if file is None:
        raise ValidationError("Provide file or path", field="file")

    if isinstance(file, (bytes, bytearray, memoryview)):
        data = bytes(file)
        return data, len(data)
    if isinstance(file, str):
        data = file.encode("utf-8")
        return data, len(data)
    if isinstance(file, Path):
        if not file.is_file():
            raise ValidationError(f"Upload path does not exist: {file}", field="file")
        handle = file.open("rb")
        return handle, file.stat().st_size
    if hasattr(file, "read"):
        size = None
        try:
            if hasattr(file, "seek") and hasattr(file, "tell"):
                pos = file.tell()
                file.seek(0, io.SEEK_END)
                size = file.tell() - pos
                file.seek(pos)
        except Exception:
            size = None
        return file, size

    raise ValidationError(
        "Unsupported file body. Use bytes, str, pathlib.Path, or a binary file object.",
        field="file",
    )


def read_all(file: Any = None, path: Optional[Union[str, Path]] = None) -> bytes:
    body, _ = resolve_body(file, path)
    if isinstance(body, bytes):
        return body
    try:
        return body.read()
    finally:
        close = getattr(body, "close", None)
        if callable(close) and not isinstance(file, io.IOBase):
            # Only close handles we opened from a Path.
            if path is not None or isinstance(file, Path):
                close()


def split_bytes(data: bytes, chunk_size: int) -> list[bytes]:
    if chunk_size < 1:
        raise ValidationError("chunk_size must be >= 1", field="chunk_size")
    return [data[index : index + chunk_size] for index in range(0, len(data), chunk_size)] or [b""]


def stream_to_bytes(stream: BinaryIO) -> bytes:
    return stream.read()
