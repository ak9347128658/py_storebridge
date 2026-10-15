from __future__ import annotations

import pytest

from py_storebridge.factory import create_storage_from_provider
from tests.fakes import MemoryProvider


@pytest.fixture
def memory_provider() -> MemoryProvider:
    return MemoryProvider(name="src")


@pytest.fixture
def storage(memory_provider: MemoryProvider):
    return create_storage_from_provider(memory_provider)


@pytest.fixture
def dest_storage():
    return create_storage_from_provider(MemoryProvider(name="dst"))
