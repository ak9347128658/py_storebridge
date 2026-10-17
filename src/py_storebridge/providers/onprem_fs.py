"""Local-disk engine for on-premises storage.

Objects live under ``{root}/buckets/{bucket}/objects``. A sidecar JSON file
holds visibility, content type, and user metadata so the object bytes stay
raw. Keys are percent-encoded per path segment. Windows reserved device names
and any segment that would decode to ``.`` or ``..`` are prefixed so a key
cannot escape the bucket directory.
"""

from __future__ import annotations

import builtins
import hashlib
import json
import os
import secrets
import shutil
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Dict, Iterator, Optional
from urllib.parse import quote, unquote

from py_storebridge.config import LogLevel, RetryConfig, Visibility
from py_storebridge.exceptions import (
    FileNotFoundError,
    PermissionError,
    ProviderError,
    ValidationError,
)
from py_storebridge.io import resolve_body
from py_storebridge.keys import validate_key
from py_storebridge.providers.base import BaseProvider, _etag
from py_storebridge.schemas import (
    CopyOptions,
    FileListEntry,
    FileMetadata,
    ListOptions,
    ListResult,
    MultipartPartResult,
    MultipartUploadSession,
    SignedUrlOptions,
    UploadOptions,
    UploadPartOptions,
    UploadResult,
)

_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"}
_WINDOWS_RESERVED.update({f"COM{i}" for i in range(1, 10)})
_WINDOWS_RESERVED.update({f"LPT{i}" for i in range(1, 10)})
_SEGMENT_PREFIX = "sb."


def _windows_unsafe(segment: str) -> bool:
    if segment in {".", ".."} or segment.endswith(".") or segment.endswith(" "):
        return True
    head = segment.rstrip(" .").split(".", 1)[0]
    return head.upper() in _WINDOWS_RESERVED


def encode_segment(segment: str) -> str:
    quoted = quote(segment, safe="")
    # ``.`` is never percent-encoded, so prefix reserved names and our own marker.
    if _windows_unsafe(segment) or quoted.startswith(_SEGMENT_PREFIX) or quoted in {".", ".."}:
        return _SEGMENT_PREFIX + quoted
    return quoted


def decode_segment(segment: str) -> str:
    if segment.startswith(_SEGMENT_PREFIX):
        segment = segment[len(_SEGMENT_PREFIX) :]
    return unquote(segment)


def _is_inside(child: Path, parent: Path) -> bool:
    child_s = os.path.normcase(os.path.abspath(child))
    parent_s = os.path.normcase(os.path.abspath(parent))
    if child_s == parent_s:
        return True
    prefix = parent_s if parent_s.endswith(os.sep) else parent_s + os.sep
    return child_s.startswith(prefix)


def _atomic_write(path: Path, chunks) -> str:
    """Write chunks to ``path`` via a temp file. Returns the MD5 hex digest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    hasher = hashlib.md5()
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            for chunk in chunks:
                if not chunk:
                    continue
                handle.write(chunk)
                hasher.update(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return hasher.hexdigest()


def load_signing_secret(root: Path, configured: Optional[str]) -> str:
    if configured:
        if len(configured) < 16:
            raise ValidationError("signing_secret must be at least 16 characters", field="signing_secret")
        return configured
    path = root / ".signing_secret"
    if path.is_file():
        secret = path.read_text(encoding="utf-8").strip()
        if len(secret) < 16:
            raise ProviderError(
                f"Signing secret in {path} is shorter than 16 characters.",
                "onprem",
            )
        return secret
    secret = secrets.token_urlsafe(32)
    root.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(secret)
    return secret


class _Record:
    def __init__(
        self,
        *,
        key: str,
        size: int,
        content_type: str,
        etag: str,
        last_modified: datetime,
        visibility: str,
        metadata: Optional[Dict[str, str]] = None,
        content_disposition: Optional[str] = None,
        content_encoding: Optional[str] = None,
        cache_control: Optional[str] = None,
    ) -> None:
        self.key = key
        self.size = size
        self.content_type = content_type
        self.etag = etag
        self.last_modified = last_modified
        self.visibility = visibility
        self.metadata = metadata or None
        self.content_disposition = content_disposition
        self.content_encoding = content_encoding
        self.cache_control = cache_control

    def to_json(self) -> dict:
        return {
            "v": 1,
            "key": self.key,
            "size": self.size,
            "content_type": self.content_type,
            "etag": self.etag,
            "last_modified": self.last_modified.isoformat(),
            "visibility": self.visibility,
            "metadata": self.metadata,
            "content_disposition": self.content_disposition,
            "content_encoding": self.content_encoding,
            "cache_control": self.cache_control,
        }

    @classmethod
    def from_json(cls, payload: dict) -> "_Record":
        modified = payload.get("last_modified")
        when = datetime.fromisoformat(modified) if isinstance(modified, str) else datetime.now(timezone.utc)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        metadata = payload.get("metadata") or None
        return cls(
            key=str(payload.get("key") or ""),
            size=int(payload.get("size") or 0),
            content_type=str(payload.get("content_type") or "application/octet-stream"),
            etag=str(payload.get("etag") or ""),
            last_modified=when,
            visibility=str(payload.get("visibility") or "private"),
            metadata=dict(metadata) if isinstance(metadata, dict) else None,
            content_disposition=payload.get("content_disposition"),
            content_encoding=payload.get("content_encoding"),
            cache_control=payload.get("cache_control"),
        )


class FilesystemBackend(BaseProvider):
    """Bucket stored on a local or mounted filesystem. No extra service."""

    provider_name = "onprem"

    def __init__(
        self,
        *,
        root: Path,
        bucket: str,
        signing_secret: str,
        base_url: str,
        custom_domain: Optional[str] = None,
        cdn_url: Optional[str] = None,
        retry: Optional[RetryConfig] = None,
        log_level: Optional[LogLevel] = None,
        default_visibility: Optional[Visibility] = None,
        max_upload_bytes: Optional[int] = None,
    ) -> None:
        super().__init__(retry=retry, log_level=log_level, default_visibility=default_visibility)
        self.root = root
        self.bucket = bucket
        self.signing_secret = signing_secret
        self.base_url = base_url.rstrip("/")
        self.custom_domain = custom_domain
        self.cdn_url = cdn_url
        self.max_upload_bytes = max_upload_bytes
        self.bucket_dir = self.root / "buckets" / bucket
        self.objects_dir = self.bucket_dir / "objects"
        self.meta_dir = self.bucket_dir / "meta"
        self.multipart_dir = self.bucket_dir / "multipart"
        self._lock = threading.RLock()
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        try:
            self.objects_dir.mkdir(parents=True, exist_ok=True)
            self.meta_dir.mkdir(parents=True, exist_ok=True)
            self.multipart_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise self._translate_os(exc) from exc

    def _translate_os(self, exc: OSError) -> Exception:
        if isinstance(exc, builtins.PermissionError):
            return PermissionError(str(exc), self.provider_name, exc)
        return ProviderError(str(exc), self.provider_name, exc)

    def _visibility_value(self, visibility: Optional[Visibility]) -> str:
        resolved = self.resolve_visibility(visibility)
        return str(resolved.value if hasattr(resolved, "value") else resolved)

    def _guard(self, path: Path, root: Path, message: str) -> Path:
        resolved = path.resolve()
        if not _is_inside(resolved, root.resolve()):
            raise PermissionError(message, self.provider_name)
        return path

    def _object_path(self, key: str) -> Path:
        parts = [encode_segment(part) for part in key.split("/")]
        path = self.objects_dir.joinpath(*parts)
        return self._guard(path, self.objects_dir, "Object path escapes the bucket directory")

    def _meta_path(self, key: str) -> Path:
        parts = [encode_segment(part) for part in key.split("/")]
        path = self.meta_dir.joinpath(*parts[:-1], parts[-1] + ".json")
        return self._guard(path, self.meta_dir, "Metadata path escapes the bucket directory")

    def _read_record(self, key: str) -> Optional[_Record]:
        path = self._meta_path(key)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderError(f"Unreadable metadata for {key}: {exc}", self.provider_name, exc) from exc
        record = _Record.from_json(payload)
        if not self._object_path(key).is_file():
            return None
        return record

    def _write_record(self, record: _Record) -> None:
        path = self._meta_path(record.key)
        data = json.dumps(record.to_json(), separators=(",", ":")).encode("utf-8")
        _atomic_write(path, [data])

    def _iter_body(self, body, size: Optional[int]):
        if self.max_upload_bytes is not None and size is not None and size > self.max_upload_bytes:
            raise ValidationError("Upload exceeds max_upload_bytes", field="file")
        if isinstance(body, bytes):
            if self.max_upload_bytes is not None and len(body) > self.max_upload_bytes:
                raise ValidationError("Upload exceeds max_upload_bytes", field="file")
            yield body
            return
        remaining = self.max_upload_bytes
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                return
            if not isinstance(chunk, bytes):
                chunk = bytes(chunk)
            if remaining is not None:
                remaining -= len(chunk)
                if remaining < 0:
                    raise ValidationError("Upload exceeds max_upload_bytes", field="file")
            yield chunk

    def _save(self, options: UploadOptions) -> UploadResult:
        key = validate_key(options.key)
        body, size = resolve_body(options.file, options.path)
        visibility = self._visibility_value(options.visibility)
        try:
            object_path = self._object_path(key)
            etag = _atomic_write(object_path, self._iter_body(body, size))
            written = object_path.stat().st_size
            record = _Record(
                key=key,
                size=written,
                content_type=options.content_type or "application/octet-stream",
                etag=etag,
                last_modified=datetime.now(timezone.utc),
                visibility=visibility,
                metadata=dict(options.metadata) if options.metadata else None,
                content_disposition=options.content_disposition,
                content_encoding=options.content_encoding,
                cache_control=options.cache_control,
            )
            self._write_record(record)
            self._chmod_object(object_path, visibility)
        except OSError as exc:
            raise self._translate_os(exc) from exc
        finally:
            if options.path is not None or isinstance(options.file, Path):
                closer = getattr(body, "close", None)
                if callable(closer):
                    closer()
        return self._result(record)

    def _chmod_object(self, path: Path, visibility: str) -> None:
        try:
            os.chmod(path, 0o644 if visibility == Visibility.PUBLIC else 0o600)
        except OSError:
            return

    def _result(self, record: _Record) -> UploadResult:
        return UploadResult(
            key=record.key,
            url=self._url_for(record.key),
            provider=self.provider_name,
            etag=record.etag,
            size=record.size,
            content_type=record.content_type,
            metadata=record.metadata,
        )

    def _metadata(self, record: _Record) -> FileMetadata:
        return FileMetadata(
            key=record.key,
            size=record.size,
            content_type=record.content_type,
            last_modified=record.last_modified,
            etag=record.etag,
            metadata=record.metadata,
            visibility=Visibility(record.visibility),
        )

    def origin(self) -> str:
        if self.custom_domain:
            return self.custom_domain.rstrip("/")
        if self.cdn_url:
            return self.cdn_url.rstrip("/")
        return self.base_url.rstrip("/")

    def _url_for(self, key: str) -> str:
        return f"{self.origin()}/{self.bucket}/{quote(key, safe='/')}"

    def _upload(self, options: UploadOptions) -> UploadResult:
        with self._lock:
            return self._save(options)

    def _delete(self, key: str) -> None:
        with self._lock:
            object_path = self._object_path(key)
            meta_path = self._meta_path(key)
            try:
                if object_path.is_file():
                    object_path.unlink()
                if meta_path.is_file():
                    meta_path.unlink()
            except OSError as exc:
                raise self._translate_os(exc) from exc

    def _exists(self, key: str) -> bool:
        with self._lock:
            return self._read_record(key) is not None

    def _get_metadata(self, key: str) -> FileMetadata:
        with self._lock:
            record = self._read_record(key)
        if record is None:
            raise FileNotFoundError(key, self.provider_name)
        return self._metadata(record)

    def _iter_keys(self) -> Iterator[str]:
        if not self.objects_dir.is_dir():
            return
        root = self.objects_dir.resolve()
        for dirpath, dirnames, filenames in os.walk(self.objects_dir):
            dirnames[:] = [name for name in dirnames if not name.startswith(".")]
            for name in filenames:
                if name.startswith("."):
                    continue
                full = Path(dirpath) / name
                resolved = full.resolve()
                if not _is_inside(resolved, root):
                    continue
                relative = Path(os.path.relpath(resolved, root))
                if relative.parts and relative.parts[0] == "..":
                    continue
                try:
                    decoded = "/".join(decode_segment(part) for part in relative.parts)
                    yield validate_key(decoded)
                except (ValidationError, ValueError):
                    continue

    def _list(self, options: Optional[ListOptions] = None) -> ListResult:
        opts = options or ListOptions()
        prefix = opts.prefix or ""
        with self._lock:
            keys = sorted(key for key in self._iter_keys() if key.startswith(prefix))
        prefixes = None
        files = keys
        if opts.delimiter:
            grouped: list[str] = []
            prefix_set = set()
            for key in keys:
                rest = key[len(prefix) :]
                if opts.delimiter in rest:
                    head = rest.split(opts.delimiter, 1)[0]
                    prefix_set.add(f"{prefix}{head}{opts.delimiter}")
                else:
                    grouped.append(key)
            files = grouped
            prefixes = sorted(prefix_set) or None
            if opts.cursor:
                prefixes = None
        if opts.cursor:
            files = [key for key in files if key > opts.cursor]
        limit = opts.limit
        page = files if limit is None else files[:limit]
        next_cursor = page[-1] if limit is not None and len(files) > limit else None
        entries = []
        for key in page:
            record = self._read_record(key)
            if record is None:
                continue
            entries.append(
                FileListEntry(
                    key=key,
                    size=record.size,
                    last_modified=record.last_modified,
                    etag=record.etag,
                )
            )
        return ListResult(files=entries, next_cursor=next_cursor, prefixes=prefixes)

    def _get_url(self, key: str) -> str:
        validate_key(key)
        return self._url_for(key)

    def _get_signed_url(self, key: str, options: Optional[SignedUrlOptions] = None) -> str:
        from py_storebridge.providers.onprem_sign import signed_query

        opts = options or SignedUrlOptions()
        expires = int(datetime.now(timezone.utc).timestamp()) + int(opts.expires_in)
        query = signed_query(
            self.signing_secret,
            opts.method,
            self.bucket,
            key,
            expires,
            opts.content_type or "",
        )
        return f"{self._url_for(key)}?{query}"

    def _copy(self, options: CopyOptions) -> UploadResult:
        with self._lock:
            source = self._read_record(options.source_key)
            if source is None:
                raise FileNotFoundError(options.source_key, self.provider_name)
            visibility = self._visibility_value(options.visibility or source.visibility)
            metadata = dict(options.metadata or source.metadata or {})
            destination = self._object_path(options.destination_key)
            source_path = self._object_path(options.source_key)
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                fd, tmp_name = tempfile.mkstemp(prefix=".tmp-", dir=str(destination.parent))
                os.close(fd)
                shutil.copyfile(source_path, tmp_name)
                os.replace(tmp_name, destination)
                record = _Record(
                    key=options.destination_key,
                    size=destination.stat().st_size,
                    content_type=options.content_type or source.content_type,
                    etag=source.etag,
                    last_modified=datetime.now(timezone.utc),
                    visibility=visibility,
                    metadata=metadata or None,
                    content_disposition=source.content_disposition,
                    content_encoding=source.content_encoding,
                    cache_control=source.cache_control,
                )
                self._write_record(record)
                self._chmod_object(destination, visibility)
            except OSError as exc:
                raise self._translate_os(exc) from exc
            return self._result(record)

    def _get_stream(self, key: str) -> BinaryIO:
        with self._lock:
            record = self._read_record(key)
            if record is None:
                raise FileNotFoundError(key, self.provider_name)
            path = self._object_path(key)
        try:
            return path.open("rb")
        except OSError as exc:
            raise self._translate_os(exc) from exc

    def _create_multipart_upload(self, key: str, content_type: Optional[str] = None) -> MultipartUploadSession:
        validate_key(key)
        upload_id = secrets.token_hex(16)
        folder = self.multipart_dir / upload_id
        try:
            (folder / "parts").mkdir(parents=True, exist_ok=False)
            manifest = {
                "key": key,
                "content_type": content_type or "application/octet-stream",
                "visibility": self._visibility_value(None),
            }
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        except OSError as exc:
            raise self._translate_os(exc) from exc
        return MultipartUploadSession(upload_id=upload_id, key=key)

    def _part_path(self, upload_id: str, part_number: int) -> Path:
        if part_number < 1:
            raise ValidationError("part_number must be >= 1", field="part_number")
        folder = (self.multipart_dir / upload_id / "parts").resolve()
        path = folder / f"{part_number:05d}"
        if not _is_inside(path, folder):
            raise PermissionError("Multipart path escapes the upload directory", self.provider_name)
        return path

    def _upload_part(self, options: UploadPartOptions) -> MultipartPartResult:
        manifest = self.multipart_dir / options.upload_id / "manifest.json"
        if not manifest.is_file():
            raise ProviderError(f'Unknown multipart session "{options.upload_id}"', self.provider_name)
        body, _ = resolve_body(options.body)
        data = body if isinstance(body, bytes) else body.read()
        if not isinstance(data, bytes):
            data = bytes(data)
        path = self._part_path(options.upload_id, options.part_number)
        try:
            _atomic_write(path, [data])
        except OSError as exc:
            raise self._translate_os(exc) from exc
        return MultipartPartResult(part_number=options.part_number, etag=_etag(data))

    def _complete_multipart_upload(
        self,
        session: MultipartUploadSession,
        parts: list,
    ) -> UploadResult:
        folder = self.multipart_dir / session.upload_id
        manifest_path = folder / "manifest.json"
        if not manifest_path.is_file():
            raise ProviderError(f'Unknown multipart session "{session.upload_id}"', self.provider_name)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        ordered = sorted(parts, key=lambda item: item.part_number)

        def chunks():
            for part in ordered:
                path = self._part_path(session.upload_id, part.part_number)
                if not path.is_file():
                    raise ProviderError(
                        f"Missing part {part.part_number} for key {session.key}",
                        self.provider_name,
                    )
                with path.open("rb") as handle:
                    while True:
                        blob = handle.read(1024 * 1024)
                        if not blob:
                            break
                        yield blob

        with self._lock:
            object_path = self._object_path(session.key)
            try:
                etag = _atomic_write(object_path, chunks())
                record = _Record(
                    key=session.key,
                    size=object_path.stat().st_size,
                    content_type=manifest.get("content_type") or "application/octet-stream",
                    etag=etag,
                    last_modified=datetime.now(timezone.utc),
                    visibility=str(manifest.get("visibility") or "private"),
                )
                self._write_record(record)
                self._chmod_object(object_path, record.visibility)
            except OSError as exc:
                raise self._translate_os(exc) from exc
        shutil.rmtree(folder, ignore_errors=True)
        return self._result(record)

    def _abort_multipart_upload(self, session: MultipartUploadSession) -> None:
        folder = self.multipart_dir / session.upload_id
        if folder.exists():
            shutil.rmtree(folder, ignore_errors=True)

    def _list_parts(self, session: MultipartUploadSession) -> list:
        folder = self.multipart_dir / session.upload_id / "parts"
        if not folder.is_dir():
            return []
        now = datetime.now(timezone.utc)
        entries = []
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.name.startswith("."):
                continue
            try:
                number = int(path.name)
            except ValueError:
                continue
            blob = path.read_bytes()
            entries.append(
                FileListEntry(
                    key=f"{session.key}#part-{number}",
                    size=len(blob),
                    last_modified=now,
                    etag=_etag(blob),
                )
            )
        return entries

    def stat(self, key: str) -> Optional[_Record]:
        with self._lock:
            return self._read_record(key)

    def write_from_path(
        self,
        key: str,
        source: Path,
        *,
        content_type: str,
        visibility: Optional[str] = None,
    ) -> _Record:
        """Commit a finished temp file as an object. Used by the embedded server."""
        validate_key(key)
        chosen = visibility or self._visibility_value(None)
        with self._lock:
            destination = self._object_path(key)
            try:
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
                hasher = hashlib.md5()
                with destination.open("rb") as handle:
                    while True:
                        blob = handle.read(1024 * 1024)
                        if not blob:
                            break
                        hasher.update(blob)
                data_etag = hasher.hexdigest()
                record = _Record(
                    key=key,
                    size=destination.stat().st_size,
                    content_type=content_type or "application/octet-stream",
                    etag=data_etag,
                    last_modified=datetime.now(timezone.utc),
                    visibility=chosen,
                )
                self._write_record(record)
                self._chmod_object(destination, chosen)
            except OSError as exc:
                raise self._translate_os(exc) from exc
            return record
