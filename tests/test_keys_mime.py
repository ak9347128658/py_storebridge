from __future__ import annotations

import pytest

from py_storebridge import ValidationError, detect_mime_type
from py_storebridge.keys import join_key, map_transfer_key, validate_key


@pytest.mark.parametrize(
    "key",
    ["a.png", "users/avatar.png", "a/b/c"],
)
def test_valid_keys(key: str) -> None:
    assert validate_key(key) == key


@pytest.mark.parametrize(
    "key",
    ["", "  ", "/leading", "trailing/", "a\\b", "a/../b", "a//b", "."],
)
def test_invalid_keys(key: str) -> None:
    with pytest.raises(ValidationError):
        validate_key(key)


def test_key_too_long() -> None:
    with pytest.raises(ValidationError):
        validate_key("a" * 1025)


def test_join_key() -> None:
    assert join_key("images/", "/2024", "a.png") == "images/2024/a.png"


def test_map_transfer_key_prefix() -> None:
    mapped = map_transfer_key(
        "images/a.png",
        source_prefix="images/",
        destination_prefix="backup/images/",
    )
    assert mapped == "backup/images/a.png"


def test_map_transfer_key_single() -> None:
    assert map_transfer_key("a.png", destination_key="b.png") == "b.png"


def test_mime_detection() -> None:
    assert detect_mime_type("photo.webp") == "image/webp"
    assert detect_mime_type("data.parquet") == "application/vnd.apache.parquet"
    assert detect_mime_type("noext") == "application/octet-stream"
    assert detect_mime_type("readme.md") == "text/markdown"
