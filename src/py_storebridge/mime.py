"""MIME type detection from object keys."""

from __future__ import annotations

import mimetypes
import os
from typing import Optional

_EXTRA = {
    ".aac": "audio/aac",
    ".avif": "image/avif",
    ".csv": "text/csv",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".flac": "audio/flac",
    ".heic": "image/heic",
    ".json": "application/json",
    ".md": "text/markdown",
    ".mkv": "video/x-matroska",
    ".parquet": "application/vnd.apache.parquet",
    ".pdf": "application/pdf",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".svg": "image/svg+xml",
    ".toml": "application/toml",
    ".wasm": "application/wasm",
    ".webm": "video/webm",
    ".webp": "image/webp",
    ".woff2": "font/woff2",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xml": "application/xml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".zip": "application/zip",
}


def detect_mime_type(key: str, fallback: str = "application/octet-stream") -> str:
    ext = os.path.splitext(key)[1].lower()
    if ext in _EXTRA:
        return _EXTRA[ext]
    guessed, _ = mimetypes.guess_type(key)
    return guessed or fallback


def detect_mime_type_optional(key: str) -> Optional[str]:
    ext = os.path.splitext(key)[1].lower()
    if ext in _EXTRA:
        return _EXTRA[ext]
    guessed, _ = mimetypes.guess_type(key)
    return guessed
