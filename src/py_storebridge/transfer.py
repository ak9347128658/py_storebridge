"""Copy or move files, folders, and entire buckets between providers."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING, Any, Iterable, List, Optional, Union

from py_storebridge.exceptions import StoreBridgeError, TransferError
from py_storebridge.keys import map_transfer_key
from py_storebridge.schemas import (
    FileListEntry,
    TransferItemResult,
    TransferItemStatus,
    TransferOptions,
    TransferResult,
    UploadOptions,
    parse_model,
)

if TYPE_CHECKING:
    from py_storebridge.client import StorageClient


def transfer(
    source: "StorageClient",
    destination: "StorageClient",
    options: Union[TransferOptions, dict, None] = None,
    **kwargs: Any,
) -> TransferResult:
    """Transfer existing objects from ``source`` to ``destination``.

    Scope (pick one):

    * ``source_key`` — a single object
    * ``keys`` — an explicit list of object keys
    * ``prefix`` — every object under a folder-like prefix
    * omitted — every object in the source bucket / container
    """
    parsed = parse_model(TransferOptions, options, **kwargs) if options or kwargs else TransferOptions()
    source.on  # noqa: B018 — keep client duck-typed
    items = list(_resolve_items(source, parsed))
    total = len(items)

    source._hooks.emit(  # noqa: SLF001 — intentional lifecycle hook
        "before:transfer",
        {
            "source": source.provider_name,
            "destination": destination.provider_name,
            "count": total,
            "options": parsed,
        },
    )

    result = TransferResult(
        source_provider=source.provider_name,
        destination_provider=destination.provider_name,
        dry_run=parsed.dry_run,
    )

    if parsed.dry_run:
        for entry in items:
            dest_key = _destination_key(entry.key, parsed)
            item = TransferItemResult(
                source_key=entry.key,
                destination_key=dest_key,
                status=TransferItemStatus.PLANNED,
                size=entry.size,
            )
            result.items.append(item)
            result.planned += 1
            result.bytes_transferred += entry.size
            if parsed.on_item:
                parsed.on_item(item)
        source._hooks.emit("after:transfer", result)  # noqa: SLF001
        return result

    def worker(entry: FileListEntry) -> TransferItemResult:
        return _transfer_one(source, destination, entry, parsed)

    completed = 0
    workers = min(parsed.concurrency, max(total, 1))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, entry): entry for entry in items}
        for future in as_completed(futures):
            try:
                item = future.result()
            except StoreBridgeError as exc:
                entry = futures[future]
                dest_key = _destination_key(entry.key, parsed)
                item = TransferItemResult(
                    source_key=entry.key,
                    destination_key=dest_key,
                    status=TransferItemStatus.FAILED,
                    size=entry.size,
                    error=str(exc),
                )
                if not parsed.continue_on_error:
                    _tally(result, item)
                    source._hooks.emit("error", {"operation": "transfer", "error": exc})  # noqa: SLF001
                    source._hooks.emit("after:transfer", result)  # noqa: SLF001
                    raise TransferError(
                        f"Transfer stopped on {entry.key}: {exc}",
                        result=result,
                        original_error=exc,
                    ) from exc
            _tally(result, item)
            completed += 1
            if parsed.on_progress:
                parsed.on_progress(completed, total, item.source_key)
            if parsed.on_item:
                parsed.on_item(item)

    source._hooks.emit("after:transfer", result)  # noqa: SLF001
    return result


def _tally(result: TransferResult, item: TransferItemResult) -> None:
    result.items.append(item)
    if item.status == TransferItemStatus.TRANSFERRED:
        result.transferred += 1
        result.bytes_transferred += item.size
    elif item.status == TransferItemStatus.SKIPPED:
        result.skipped += 1
    elif item.status == TransferItemStatus.FAILED:
        result.failed += 1


def _destination_key(source_key: str, options: TransferOptions) -> str:
    return map_transfer_key(
        source_key,
        source_prefix=options.prefix,
        destination_prefix=options.destination_prefix,
        destination_key=options.destination_key,
    )


def _resolve_items(source: "StorageClient", options: TransferOptions) -> Iterable[FileListEntry]:
    if options.source_key:
        meta = source.get_metadata(options.source_key)
        yield FileListEntry(
            key=meta.key,
            size=meta.size,
            last_modified=meta.last_modified,
            etag=meta.etag,
        )
        return
    if options.keys:
        # Defer existence checks to the per-item transfer so continue_on_error applies.
        for key in options.keys:
            yield FileListEntry(key=key, size=0)
        return
    yield from source.iter_files(prefix=options.prefix)


def _transfer_one(
    source: "StorageClient",
    destination: "StorageClient",
    entry: FileListEntry,
    options: TransferOptions,
) -> TransferItemResult:
    dest_key = _destination_key(entry.key, options)
    try:
        if options.skip_existing and destination.exists(dest_key):
            return TransferItemResult(
                source_key=entry.key,
                destination_key=dest_key,
                status=TransferItemStatus.SKIPPED,
                size=entry.size,
            )
        if not options.overwrite and destination.exists(dest_key):
            return TransferItemResult(
                source_key=entry.key,
                destination_key=dest_key,
                status=TransferItemStatus.SKIPPED,
                size=entry.size,
            )

        meta = source.get_metadata(entry.key)
        body = source.get_buffer(entry.key)
        uploaded = destination.upload(
            UploadOptions(
                key=dest_key,
                file=body,
                content_type=meta.content_type,
                visibility=meta.visibility if options.preserve_visibility else None,
                metadata=meta.metadata if options.preserve_metadata else None,
            )
        )
        if options.delete_source:
            source.delete(key=entry.key)
        return TransferItemResult(
            source_key=entry.key,
            destination_key=dest_key,
            status=TransferItemStatus.TRANSFERRED,
            size=meta.size or len(body),
            url=uploaded.url,
        )
    except StoreBridgeError as exc:
        if not options.continue_on_error:
            raise
        return TransferItemResult(
            source_key=entry.key,
            destination_key=dest_key,
            status=TransferItemStatus.FAILED,
            size=entry.size,
            error=str(exc),
        )
