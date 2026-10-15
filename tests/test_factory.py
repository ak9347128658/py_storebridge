from __future__ import annotations

import pytest

from py_storebridge import MissingDependencyError, ValidationError, create_storage


def test_unknown_provider() -> None:
    with pytest.raises(ValidationError):
        create_storage({"provider": "drive", "credentials": {"bucket": "x"}})


def test_s3_missing_sdk_raises_dependency_error() -> None:
    try:
        import boto3  # noqa: F401
    except ImportError:
        with pytest.raises(MissingDependencyError) as exc:
            create_storage(
                {
                    "provider": "s3",
                    "credentials": {
                        "access_key_id": "AKI",
                        "secret_access_key": "SECRET",
                        "region": "us-east-1",
                        "bucket": "demo",
                    },
                }
            )
        assert "py_storebridge[s3]" in str(exc.value)
        return
    pytest.skip("boto3 is installed in this environment")
