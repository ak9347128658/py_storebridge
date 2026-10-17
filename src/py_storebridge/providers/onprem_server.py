"""Optional stdlib HTTP server for on-premises public files and signed URLs.

Binds to the configured host (127.0.0.1 by default). Public objects are
readable without a token. Private objects are indistinguishable from missing
objects until the request carries a valid HMAC token. PUT and DELETE always
require a token, including when the object is public, so an anonymous client
cannot replace a public file.
"""

from __future__ import annotations

import atexit
import os
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qs, unquote, urlsplit

from py_storebridge.exceptions import PermissionError, ProviderError, ValidationError
from py_storebridge.keys import validate_key
from py_storebridge.providers.onprem_fs import FilesystemBackend
from py_storebridge.providers.onprem_sign import SIGNATURE_PARAM, verify

def _header_value(value: Optional[str], fallback: str) -> str:
    if not value:
        return fallback
    if any(char in value for char in "\r\n"):
        return fallback
    return value


def _connect_host(bind_host: str) -> str:
    if bind_host in {"0.0.0.0", "::", ""}:
        return "127.0.0.1"
    return bind_host


class OnPremServer:
    def __init__(
        self,
        backend: FilesystemBackend,
        host: str,
        port: int,
        *,
        cors_origin: Optional[str] = None,
    ) -> None:
        self.backend = backend
        self.host = host
        self.port = port
        self.cors_origin = cors_origin
        self.base_url = ""
        self._httpd: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> str:
        handler = _handler_for(self.backend, self.cors_origin)
        try:
            self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        except OSError as exc:
            raise ProviderError(
                f"Could not bind the on-premises file server to {self.host}:{self.port}: {exc}",
                "onprem",
                exc,
            ) from exc
        self._httpd.daemon_threads = True
        self.port = int(self._httpd.server_address[1])
        connect_host = _connect_host(self.host)
        self.base_url = f"http://{connect_host}:{self.port}"
        self._thread = threading.Thread(
            target=self._httpd.serve_forever,
            kwargs={"poll_interval": 0.1},
            name="py-storebridge-onprem",
            daemon=True,
        )
        self._thread.start()
        self._wait_until_listening(connect_host)
        atexit.register(self.stop)
        return self.base_url

    def _wait_until_listening(self, connect_host: str) -> None:
        import socket

        deadline = time.time() + 3
        while time.time() < deadline:
            try:
                with socket.create_connection((connect_host, self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.02)
        self.stop()
        raise ProviderError("On-premises file server did not start listening", "onprem")

    def stop(self) -> None:
        httpd = self._httpd
        if httpd is None:
            return
        self._httpd = None
        try:
            atexit.unregister(self.stop)
        except Exception:
            pass
        httpd.shutdown()
        httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None


def _handler_for(backend: FilesystemBackend, cors_origin: Optional[str]):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def log_message(self, fmt: str, *args) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            self._read("GET")

        def do_HEAD(self) -> None:  # noqa: N802
            self._read("HEAD")

        def do_PUT(self) -> None:  # noqa: N802
            self._put()

        def do_DELETE(self) -> None:  # noqa: N802
            self._delete()

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send(204, b"", "text/plain")

        def _query(self):
            return parse_qs(urlsplit(self.path).query, keep_blank_values=True)

        def _object_key(self) -> Optional[str]:
            raw = unquote(urlsplit(self.path).path)
            if "\\" in raw or "\x00" in raw:
                self._send(400, b"Invalid path", "text/plain")
                return None
            parts = [part for part in raw.split("/") if part != ""]
            if any(part in {".", ".."} for part in parts):
                self._send(400, b"Invalid path", "text/plain")
                return None
            if len(parts) < 2 or parts[0] != backend.bucket:
                self._send(404, b"Not found", "text/plain")
                return None
            key = "/".join(parts[1:])
            try:
                return validate_key(key)
            except ValidationError:
                self._send(400, b"Invalid key", "text/plain")
                return None

        def _authorized(self, method: str, key: str) -> bool:
            query = self._query()
            header_type = self.headers.get("Content-Type", "")
            reason = verify(
                backend.signing_secret,
                method,
                backend.bucket,
                key,
                query,
                now=time.time(),
                header_content_type=header_type.split(";", 1)[0].strip(),
            )
            if reason is None:
                return True
            self._send(403, reason.encode("utf-8"), "text/plain")
            return False

        def _read(self, method: str) -> None:
            key = self._object_key()
            if key is None:
                return
            query = self._query()
            signed = SIGNATURE_PARAM in query
            try:
                record = backend.stat(key)
            except (PermissionError, ProviderError):
                self._send(403, b"Forbidden", "text/plain")
                return
            if not signed:
                if record is not None and str(record.visibility) == "public" and method in {"GET", "HEAD"}:
                    self._serve_record(record, method)
                    return
                self._send(404, b"Not found", "text/plain")
                return
            if not self._authorized(method, key):
                return
            if record is None:
                self._send(404, b"Not found", "text/plain")
                return
            self._serve_record(record, method)

        def _serve_record(self, record, method: str) -> None:
            path = backend._object_path(record.key)
            content_type = _header_value(record.content_type, "application/octet-stream")
            extra = {}
            if record.content_disposition:
                extra["Content-Disposition"] = _header_value(record.content_disposition, "attachment")
            if record.cache_control:
                extra["Cache-Control"] = _header_value(record.cache_control, "private")
            if method == "HEAD":
                self._send(200, b"", content_type, extra=extra, content_length=record.size)
                return
            try:
                handle = path.open("rb")
            except OSError:
                self._send(404, b"Not found", "text/plain")
                return
            self._send(200, b"", content_type, extra=extra, content_length=record.size)
            try:
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
            finally:
                handle.close()

        def _put(self) -> None:
            key = self._object_key()
            if key is None:
                return
            if not self._authorized("PUT", key):
                return
            length = self.headers.get("Content-Length")
            if length is None or not length.isdigit():
                self._send(411, b"Content-Length required", "text/plain")
                return
            total = int(length)
            if backend.max_upload_bytes is not None and total > backend.max_upload_bytes:
                self._send(413, b"Upload too large", "text/plain")
                return
            content_type = self.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip()
            fd, tmp_name = tempfile.mkstemp(prefix=".tmp-upload-", dir=str(backend.bucket_dir))
            try:
                remaining = total
                with os.fdopen(fd, "wb") as handle:
                    while remaining:
                        chunk = self.rfile.read(min(1024 * 1024, remaining))
                        if not chunk:
                            break
                        handle.write(chunk)
                        remaining -= len(chunk)
                if remaining:
                    self._send(400, b"Truncated upload", "text/plain")
                    return
                backend.write_from_path(key, Path(tmp_name), content_type=content_type)
                tmp_name = ""
            except (PermissionError, ProviderError, ValidationError) as exc:
                self._send(400, str(exc).encode("utf-8"), "text/plain")
                return
            finally:
                if tmp_name:
                    try:
                        os.unlink(tmp_name)
                    except OSError:
                        pass
            self._send(201, b"", "text/plain")

        def _delete(self) -> None:
            key = self._object_key()
            if key is None:
                return
            if not self._authorized("DELETE", key):
                return
            try:
                backend._delete(key)
            except (PermissionError, ProviderError) as exc:
                self._send(400, str(exc).encode("utf-8"), "text/plain")
                return
            self._send(204, b"", "text/plain")

        def _send(
            self,
            status: int,
            body: bytes,
            content_type: str,
            *,
            extra: Optional[dict] = None,
            content_length: Optional[int] = None,
        ) -> None:
            payload = body if status != 204 else b""
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(record_len(payload, content_length)))
            self.send_header("X-Content-Type-Options", "nosniff")
            if cors_origin:
                self.send_header("Access-Control-Allow-Origin", cors_origin)
                self.send_header("Access-Control-Allow-Methods", "GET, HEAD, PUT, DELETE, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
            if extra:
                for name, value in extra.items():
                    if value and "\r" not in value and "\n" not in value:
                        self.send_header(name, value)
            self.end_headers()
            if payload and self.command != "HEAD":
                self.wfile.write(payload)

    return Handler


def record_len(payload: bytes, content_length: Optional[int]) -> int:
    if content_length is not None and not payload:
        return content_length
    return len(payload)
