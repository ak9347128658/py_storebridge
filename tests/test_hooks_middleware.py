from __future__ import annotations

from py_storebridge.middleware import MiddlewareContext
from py_storebridge.schemas import UploadResult


def test_hooks_fire_in_order(storage) -> None:
    events = []

    storage.on("before:upload", lambda payload: events.append(("before", payload.key)))
    storage.on("after:upload", lambda payload: events.append(("after", payload.key)))
    storage.upload(key="hook.txt", file=b"1")
    assert events == [("before", "hook.txt"), ("after", "hook.txt")]

    storage.on("before:delete", lambda payload: events.append(("before-del", payload.key)))
    storage.delete(key="hook.txt")
    assert ("before-del", "hook.txt") in events


def test_unsubscribe_hook(storage) -> None:
    seen = []

    def handler(result: UploadResult) -> None:
        seen.append(result.key)

    storage.on("after:upload", handler)
    storage.upload(key="once.txt", file=b"1")
    storage.off("after:upload", handler)
    storage.upload(key="twice.txt", file=b"2")
    assert seen == ["once.txt"]


def test_error_hook(storage) -> None:
    captured = []
    storage.on("error", lambda payload: captured.append(payload["operation"]))
    try:
        storage.get_signed_url("missing.txt")
    except Exception:
        pass
    assert captured == ["getSignedUrl"]


def test_middleware_mutates_key_and_metadata(storage) -> None:
    def prefix(ctx: MiddlewareContext, nxt):
        ctx.options.key = f"uploads/{ctx.options.key}"
        ctx.options.metadata = {**(ctx.options.metadata or {}), "via": "mw"}
        return nxt()

    storage.use(prefix)
    result = storage.upload(key="photo.jpg", file=b"img")
    assert result.key == "uploads/photo.jpg"
    meta = storage.get_metadata("uploads/photo.jpg")
    assert meta.metadata == {"via": "mw"}


def test_middleware_onion_order(storage) -> None:
    order = []

    def outer(ctx, nxt):
        order.append("outer-in")
        result = nxt()
        order.append("outer-out")
        return result

    def inner(ctx, nxt):
        order.append("inner-in")
        result = nxt()
        order.append("inner-out")
        return result

    storage.use(outer)
    storage.use(inner)
    storage.upload(key="x.bin", file=b"x")
    assert order == ["outer-in", "inner-in", "inner-out", "outer-out"]
