"""Lazy optional-dependency loading for provider SDKs."""

from __future__ import annotations

import importlib
from typing import Any

from py_storebridge.exceptions import MissingDependencyError


def require(module: str, extra: str) -> Any:
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise MissingDependencyError(module, extra) from exc
