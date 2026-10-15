from __future__ import annotations

from py_storebridge import (
    FileNotFoundError,
    StoreBridgeError,
    StorixError,
    TransferError,
)


def test_storix_alias() -> None:
    assert StorixError is StoreBridgeError


def test_file_not_found_fields() -> None:
    err = FileNotFoundError("a.png", "s3")
    assert err.code == "FILE_NOT_FOUND"
    assert err.status_code == 404
    assert err.key == "a.png"
    assert "a.png" in str(err)
    assert "[s3]" in str(err)


def test_transfer_error_carries_result() -> None:
    err = TransferError("stopped", result={"failed": 1})
    assert err.code == "TRANSFER_ERROR"
    assert err.result == {"failed": 1}
