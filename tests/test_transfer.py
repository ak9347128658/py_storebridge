from __future__ import annotations

import pytest

from py_storebridge import TransferError, TransferItemStatus, transfer


def _seed(storage, mapping: dict[str, bytes]) -> None:
    for key, body in mapping.items():
        storage.upload(key=key, file=body, metadata={"origin": key})


def test_transfer_single_file(storage, dest_storage) -> None:
    _seed(storage, {"photo.jpg": b"img-bytes"})
    result = transfer(
        storage,
        dest_storage,
        source_key="photo.jpg",
        destination_key="migrated/photo.jpg",
    )
    assert result.ok
    assert result.transferred == 1
    assert dest_storage.get_buffer("migrated/photo.jpg") == b"img-bytes"
    assert storage.exists("photo.jpg")


def test_transfer_folder_prefix(storage, dest_storage) -> None:
    _seed(
        storage,
        {
            "images/a.png": b"a",
            "images/b.png": b"b",
            "docs/readme.md": b"md",
        },
    )
    result = storage.transfer_to(
        dest_storage,
        prefix="images/",
        destination_prefix="backup/images/",
    )
    assert result.transferred == 2
    assert dest_storage.exists("backup/images/a.png")
    assert dest_storage.exists("backup/images/b.png")
    assert not dest_storage.exists("docs/readme.md")


def test_transfer_entire_bucket(storage, dest_storage) -> None:
    _seed(storage, {"one": b"1", "two/nested": b"2"})
    result = transfer(storage, dest_storage)
    assert result.transferred == 2
    assert dest_storage.get_buffer("two/nested") == b"2"


def test_transfer_explicit_keys(storage, dest_storage) -> None:
    _seed(storage, {"a": b"a", "b": b"b", "c": b"c"})
    result = transfer(storage, dest_storage, keys=["a", "c"])
    assert result.transferred == 2
    assert dest_storage.exists("a")
    assert not dest_storage.exists("b")


def test_transfer_move_deletes_source(storage, dest_storage) -> None:
    _seed(storage, {"tmp/file.bin": b"data"})
    result = transfer(
        storage,
        dest_storage,
        source_key="tmp/file.bin",
        delete_source=True,
    )
    assert result.transferred == 1
    assert dest_storage.exists("tmp/file.bin")
    assert not storage.exists("tmp/file.bin")


def test_skip_existing(storage, dest_storage) -> None:
    _seed(storage, {"keep.txt": b"new"})
    dest_storage.upload(key="keep.txt", file=b"old")
    result = transfer(storage, dest_storage, source_key="keep.txt", skip_existing=True)
    assert result.skipped == 1
    assert dest_storage.get_buffer("keep.txt") == b"old"


def test_overwrite(storage, dest_storage) -> None:
    _seed(storage, {"keep.txt": b"new"})
    dest_storage.upload(key="keep.txt", file=b"old")
    result = transfer(storage, dest_storage, source_key="keep.txt", overwrite=True)
    assert result.transferred == 1
    assert dest_storage.get_buffer("keep.txt") == b"new"


def test_dry_run_does_not_write(storage, dest_storage) -> None:
    _seed(storage, {"a.png": b"a", "b.png": b"b"})
    result = transfer(storage, dest_storage, dry_run=True)
    assert result.planned == 2
    assert result.transferred == 0
    assert result.dry_run is True
    assert not dest_storage.exists("a.png")


def test_preserve_metadata(storage, dest_storage) -> None:
    storage.upload(key="meta.txt", file=b"x", metadata={"author": "ada"})
    transfer(storage, dest_storage, source_key="meta.txt", preserve_metadata=True)
    meta = dest_storage.get_metadata("meta.txt")
    assert meta.metadata == {"author": "ada"}


def test_continue_on_error(storage, dest_storage) -> None:
    _seed(storage, {"ok.txt": b"ok"})
    result = transfer(
        storage,
        dest_storage,
        keys=["ok.txt", "missing.txt"],
        continue_on_error=True,
    )
    assert result.transferred == 1
    assert result.failed == 1
    statuses = {item.source_key: item.status for item in result.items}
    assert statuses["ok.txt"] == TransferItemStatus.TRANSFERRED
    assert statuses["missing.txt"] == TransferItemStatus.FAILED


def test_stop_on_error(storage, dest_storage) -> None:
    with pytest.raises(TransferError) as exc:
        transfer(
            storage,
            dest_storage,
            keys=["missing.txt"],
            continue_on_error=False,
        )
    assert exc.value.result is not None


def test_progress_and_item_callbacks(storage, dest_storage) -> None:
    _seed(storage, {"a": b"a", "b": b"b"})
    progress = []
    items = []
    transfer(
        storage,
        dest_storage,
        on_progress=lambda done, total, key: progress.append((done, total, key)),
        on_item=lambda item: items.append(item.status),
    )
    assert len(progress) == 2
    assert progress[-1][0] == 2
    assert all(status == TransferItemStatus.TRANSFERRED for status in items)


def test_transfer_hooks(storage, dest_storage) -> None:
    _seed(storage, {"z": b"z"})
    seen = []
    storage.on("before:transfer", lambda payload: seen.append("before"))
    storage.on("after:transfer", lambda payload: seen.append("after"))
    transfer(storage, dest_storage, source_key="z")
    assert seen == ["before", "after"]
