"""Lifecycle hooks that fire around storage operations."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, DefaultDict, List

HookEvent = str
HookHandler = Callable[[Any], None]


class HookSystem:
    def __init__(self) -> None:
        self._handlers: DefaultDict[str, List[HookHandler]] = defaultdict(list)

    def on(self, event: HookEvent, handler: HookHandler) -> None:
        if handler not in self._handlers[event]:
            self._handlers[event].append(handler)

    def off(self, event: HookEvent, handler: HookHandler) -> None:
        handlers = self._handlers.get(event)
        if not handlers:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            return

    def emit(self, event: HookEvent, payload: Any = None) -> None:
        for handler in list(self._handlers.get(event, ())):
            handler(payload)


HOOK_EVENTS = (
    "before:upload",
    "after:upload",
    "before:delete",
    "after:delete",
    "before:copy",
    "after:copy",
    "before:move",
    "after:move",
    "before:list",
    "after:list",
    "before:getSignedUrl",
    "after:getSignedUrl",
    "before:transfer",
    "after:transfer",
    "error",
)
