"""Exponential backoff with full jitter."""

from __future__ import annotations

import random
import time
from typing import Callable, Optional, TypeVar

from py_storebridge.config import RetryConfig
from py_storebridge.exceptions import (
    FileNotFoundError,
    MaxRetriesExceededError,
    PermissionError,
    StoreBridgeError,
    ValidationError,
)
from py_storebridge.logging import Logger

T = TypeVar("T")

_NO_RETRY = (FileNotFoundError, PermissionError, ValidationError)


def merge_retry_config(retry: Optional[RetryConfig] = None) -> RetryConfig:
    return retry if retry is not None else RetryConfig()


def with_retry(
    fn: Callable[[], T],
    config: RetryConfig,
    provider: str,
    logger: Optional[Logger] = None,
) -> T:
    last_error: Optional[BaseException] = None
    attempts = config.max_retries + 1

    for attempt in range(attempts):
        try:
            return fn()
        except _NO_RETRY:
            raise
        except StoreBridgeError as exc:
            last_error = exc
            if exc.status_code and exc.status_code not in config.retryable_status_codes:
                if exc.status_code < 500 and exc.status_code not in (408, 429):
                    raise
            if attempt >= config.max_retries:
                break
            _sleep(config, attempt, logger, provider, exc)
        except Exception as exc:  # noqa: BLE001 — retry then wrap
            last_error = exc
            if attempt >= config.max_retries:
                break
            _sleep(config, attempt, logger, provider, exc)

    raise MaxRetriesExceededError(attempts, provider, last_error)


def _sleep(
    config: RetryConfig,
    attempt: int,
    logger: Optional[Logger],
    provider: str,
    error: BaseException,
) -> None:
    cap = min(config.max_delay, config.base_delay * (2 ** attempt))
    delay_ms = random.uniform(0, cap)  # full jitter
    if logger is not None:
        logger.warn(
            "retry",
            provider=provider,
            attempt=attempt + 1,
            delay_ms=round(delay_ms, 1),
            error=str(error),
        )
    time.sleep(delay_ms / 1000.0)
