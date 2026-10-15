"""Onion-model middleware chain for upload()."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from py_storebridge.schemas import UploadOptions, UploadResult

NextFn = Callable[[], UploadResult]
Middleware = Callable[["MiddlewareContext", NextFn], UploadResult]


@dataclass
class MiddlewareContext:
    options: UploadOptions
    provider: str
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Dict[str, Any] = field(default_factory=dict)
    result: Optional[UploadResult] = None


class MiddlewareChain:
    def __init__(self) -> None:
        self._stack: List[Middleware] = []

    def use(self, middleware: Middleware) -> None:
        self._stack.append(middleware)

    def execute(
        self,
        ctx: MiddlewareContext,
        core: Callable[[MiddlewareContext], UploadResult],
    ) -> UploadResult:
        def bind(mw: Middleware, nxt: Callable[[MiddlewareContext], UploadResult]):
            def wrapped(current: MiddlewareContext) -> UploadResult:
                return mw(current, lambda: nxt(current))

            return wrapped

        handler: Callable[[MiddlewareContext], UploadResult] = core
        for middleware in reversed(self._stack):
            handler = bind(middleware, handler)
        return handler(ctx)
