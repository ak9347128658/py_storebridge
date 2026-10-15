"""Thin logger wrapper around the standard library."""

from __future__ import annotations

import logging
from typing import Any

from py_storebridge.config import LogLevel

_LEVELS = {
    LogLevel.DEBUG: logging.DEBUG,
    LogLevel.INFO: logging.INFO,
    LogLevel.WARN: logging.WARNING,
    LogLevel.ERROR: logging.ERROR,
    LogLevel.SILENT: logging.CRITICAL + 1,
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "error": logging.ERROR,
    "silent": logging.CRITICAL + 1,
}


class Logger:
    def __init__(self, level: Any = LogLevel.WARN, name: str = "py_storebridge") -> None:
        self._logger = logging.getLogger(name)
        numeric = _LEVELS.get(level, logging.WARNING)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s"))
            self._logger.addHandler(handler)
            self._logger.propagate = False
        self._logger.setLevel(numeric)

    def debug(self, message: str, **fields: Any) -> None:
        self._logger.debug(_fmt(message, fields))

    def info(self, message: str, **fields: Any) -> None:
        self._logger.info(_fmt(message, fields))

    def warn(self, message: str, **fields: Any) -> None:
        self._logger.warning(_fmt(message, fields))

    def error(self, message: str, **fields: Any) -> None:
        self._logger.error(_fmt(message, fields))


def _fmt(message: str, fields: dict[str, Any]) -> str:
    if not fields:
        return message
    extras = " ".join(f"{key}={value!r}" for key, value in fields.items())
    return f"{message} {extras}"
