from __future__ import annotations

from pathlib import Path

import pytest

from py_storebridge import FileNotFoundError, ValidationError, Visibility, create_storage_from_provider
from tests.fakes import MemoryProvider


def test_upload_download_roundtrip(storage) -> None:
    result = storage.upload(key="hello.txt", file=b"hello world", content_type="text/plain")
    assert result.key == "hello.txt"
    assert result.provider == "src"
    assert storage.exists("hello.txt")
    assert storage.get_buffer("hello.txt") == b"hello world"
    meta = storage.get_metadata("hello.txt")
    assert meta.size == 11
    assert meta.content_type == "text/plain"


def test_upload_from_path(storage, tmp_path: Path) -> None:
    path = tmp_path / "note.md"
    path.write_text("docs", encoding="utf-8")
    result = storage.upload_file(path, key="docs/note.md")
    assert result.key == "docs/note.md"
    assert storage.get_buffer("docs/note.md") == b"docs"


def test_delete_and_missing_metadata(storage) -> None:
    storage.upload(key="gone.bin", file=b"x")
    storage.delete(key="gone.bin")
    assert storage.exists("gone.bin") is False
    with pytest.raises(FileNotFoundError) as exc:
        storage.get_metadata("gone.bin")
    assert exc.value.key == "gone.bin"
    assert exc.value.status_code == 404


def test_list_prefix_and_delimiter(storage) -> None:
    storage.upload(key="images/a.png", file=b"a")
    storage.upload(key="images/b.png", file=b"b")
    storage.upload(key="other/c.png", file=b"c")
    listed = storage.list(prefix="images/")
    keys = {item.key for item in listed.files}
    assert keys == {"images/a.png", "images/b.png"}
    nested = storage.list(prefix="", delimiter="/")
    assert "images/" in (nested.prefixes or [])


def test_copy_move_rename(storage) -> None:
    storage.upload(key="original.png", file=b"img", visibility=Visibility.PRIVATE)
    copied = storage.copy(source_key="original.png", destination_key="copies/1.png")
    assert copied.key == "copies/1.png"
    assert storage.exists("original.png")
    moved = storage.move(source_key="copies/1.png", destination_key="final/1.png")
    assert moved.key == "final/1.png"
    assert not storage.exists("copies/1.png")
    renamed = storage.rename("final/1.png", "final/2.png")
    assert renamed.key == "final/2.png"
    assert storage.get_buffer("final/2.png") == b"img"


def test_signed_url_and_public_url(storage) -> None:
    storage.upload(key="private/report.pdf", file=b"%PDF")
    url = storage.get_url("private/report.pdf")
    assert url.endswith("private/report.pdf")
    signed = storage.get_signed_url("private/report.pdf", expires_in=90, method="GET")
    assert "expires=90" in signed


def test_multipart_fallback(storage) -> None:
    payload = b"abcdefghij" * 100
    result = storage.upload_multipart({"key": "big.bin", "file": payload}, chunk_size=50)
    assert storage.get_buffer("big.bin") == payload
    assert result.provider == "src"


def test_delete_prefix(storage) -> None:
    storage.upload(key="tmp/a", file=b"1")
    storage.upload(key="tmp/b", file=b"2")
    storage.upload(key="keep/c", file=b"3")
    deleted = storage.delete_prefix("tmp/")
    assert deleted == 2
    assert storage.exists("keep/c")
    assert not storage.exists("tmp/a")


def test_invalid_upload_rejected(storage) -> None:
    with pytest.raises(ValidationError):
        storage.upload(key="/bad.png", file=b"x")
    with pytest.raises(ValidationError):
        storage.upload(key="ok.png")


def test_create_storage_from_provider_name() -> None:
    client = create_storage_from_provider(MemoryProvider(name="custom"))
    assert client.provider_name == "custom"


def test_download_file(storage, tmp_path: Path) -> None:
    storage.upload(key="a/b.txt", file=b"xyz")
    dest = tmp_path / "out" / "b.txt"
    storage.download_file("a/b.txt", dest)
    assert dest.read_bytes() == b"xyz"
