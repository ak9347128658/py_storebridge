from __future__ import annotations

import pytest

from py_storebridge import FileNotFoundError, MaxRetriesExceededError, ProviderError
from py_storebridge.config import RetryConfig
from py_storebridge.retry import with_retry


def test_retry_eventually_succeeds() -> None:
    state = {"n": 0}

    def flaky() -> str:
        state["n"] += 1
        if state["n"] < 3:
            raise ProviderError("boom", "memory", status_code=503)
        return "ok"

    result = with_retry(flaky, RetryConfig(max_retries=3, base_delay=1, max_delay=1), "memory")
    assert result == "ok"
    assert state["n"] == 3


def test_retry_gives_up() -> None:
    def always() -> None:
        raise ProviderError("down", "memory", status_code=503)

    with pytest.raises(MaxRetriesExceededError) as exc:
        with_retry(always, RetryConfig(max_retries=2, base_delay=1, max_delay=1), "memory")
    assert exc.value.attempts == 3


def test_not_found_is_not_retried() -> None:
    calls = {"n": 0}

    def missing() -> None:
        calls["n"] += 1
        raise FileNotFoundError("gone.png", "memory")

    with pytest.raises(FileNotFoundError):
        with_retry(missing, RetryConfig(max_retries=5, base_delay=1, max_delay=1), "memory")
    assert calls["n"] == 1
