from __future__ import annotations

import pytest

from py_storebridge import ValidationError, Visibility, parse_storage_config
from py_storebridge.config import RetryConfig, S3Config


def test_s3_config_accepts_snake_and_camel_case() -> None:
    parsed = parse_storage_config(
        {
            "provider": "s3",
            "credentials": {
                "accessKeyId": "AKI",
                "secretAccessKey": "SECRET",
                "region": "us-east-1",
                "bucket": "demo",
            },
            "visibility": "public",
        }
    )
    assert parsed.provider == "s3"
    assert parsed.credentials.access_key_id == "AKI"
    assert parsed.visibility == Visibility.PUBLIC


def test_unknown_provider_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_storage_config({"provider": "ftp", "credentials": {}})


def test_missing_bucket_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse_storage_config(
            {
                "provider": "s3",
                "credentials": {
                    "access_key_id": "AKI",
                    "secret_access_key": "SECRET",
                    "region": "us-east-1",
                },
            }
        )


def test_retry_delay_validation() -> None:
    with pytest.raises(Exception):
        RetryConfig(base_delay=1000, max_delay=10)


def test_azure_requires_auth() -> None:
    with pytest.raises(ValidationError):
        parse_storage_config(
            {
                "provider": "azure",
                "credentials": {
                    "account_name": "acct",
                    "container_name": "uploads",
                },
            }
        )


def test_r2_config() -> None:
    parsed = parse_storage_config(
        {
            "provider": "r2",
            "credentials": {
                "account_id": "abc",
                "access_key_id": "id",
                "secret_access_key": "secret",
                "bucket": "files",
            },
        }
    )
    assert parsed.provider == "r2"


def test_existing_model_passes_through() -> None:
    model = S3Config.model_validate(
        {
            "provider": "s3",
            "credentials": {
                "access_key_id": "AKI",
                "secret_access_key": "SECRET",
                "region": "eu-west-1",
                "bucket": "demo",
            },
        }
    )
    assert parse_storage_config(model) is model
