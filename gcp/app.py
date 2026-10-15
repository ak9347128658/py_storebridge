"""Google Cloud Storage upload, read, and migration test for py_storebridge 1.0.0."""

from __future__ import annotations

import io
import json
import mimetypes
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_file
from py_storebridge import StoreBridgeError, __version__
from werkzeug.exceptions import HTTPException

import migrate
from private_storage import client as private_client
from private_storage import missing as private_missing
from public_storage import client as public_client
from public_storage import missing as public_missing

PROVIDER_NAME = "Google Cloud Storage"
LIBRARY_PROVIDER = "gcs"
PORT = 8003
DOCS_PATH = "docs/gcp.md"
MIGRATION_TARGETS = [
    {"id": "aws", "label": "AWS S3"},
    {"id": "azure", "label": "Azure Blob"},
]

HERE = Path(__file__).resolve().parent
CATALOG = HERE / "data" / "uploads.json"
LOCK = threading.Lock()
MAX_EMBED = 1_000_000
TEXT_TYPES = {
    "application/json",
    "application/xml",
    "application/javascript",
    "image/svg+xml",
}

load_dotenv(HERE.parent / ".env")
load_dotenv(HERE / ".env", override=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 32 * 1024 * 1024


def _storage(visibility: str):
    if visibility == "public":
        return public_client()
    if visibility == "private":
        return private_client()
    raise ValueError("visibility must be public or private")


def _safe_name(name: str) -> str:
    base = Path(name or "upload.bin").name
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in base).strip("._")
    return (cleaned[:120] or "upload.bin")


def _content_type(filename: str, browser_type: str | None) -> str:
    browser = (browser_type or "").split(";")[0].strip().lower()
    if browser and browser != "application/octet-stream":
        return browser
    guessed, _encoding = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def _embed(data: bytes, content_type: str) -> dict:
    if len(data) > MAX_EMBED:
        return {"content_text": None, "content_base64": None, "content_omitted": True}
    textual = content_type.startswith("text/") or content_type in TEXT_TYPES
    if textual:
        try:
            return {
                "content_text": data.decode("utf-8"),
                "content_base64": None,
                "content_omitted": False,
            }
        except UnicodeDecodeError:
            pass
    import base64

    return {
        "content_text": None,
        "content_base64": base64.b64encode(data).decode("ascii"),
        "content_omitted": False,
    }


def _read_catalog() -> dict:
    if not CATALOG.exists():
        return {"provider": LIBRARY_PROVIDER, "uploads": []}
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _write_catalog(catalog: dict) -> None:
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")


def _library_error(exc: StoreBridgeError):
    return jsonify({"error": str(exc), "code": getattr(exc, "code", None)}), 502


@app.get("/")
def index():
    html = (HERE / "index.html").read_text(encoding="utf-8")
    response = app.response_class(html, mimetype="text/html; charset=utf-8")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/info")
def info():
    return jsonify(
        {
            "name": PROVIDER_NAME,
            "library": f"py_storebridge {__version__}",
            "library_provider": LIBRARY_PROVIDER,
            "port": PORT,
            "docs": DOCS_PATH,
            "catalog": "data/uploads.json",
            "migration_targets": MIGRATION_TARGETS,
            "max_embedded_bytes": MAX_EMBED,
        }
    )


@app.get("/api/health")
def health():
    public_gaps = public_missing()
    private_gaps = private_missing()
    return jsonify(
        {
            "ok": not public_gaps and not private_gaps,
            "public_ready": not public_gaps,
            "private_ready": not private_gaps,
            "missing_public": public_gaps,
            "missing_private": private_gaps,
            "library": __version__,
        }
    )


@app.get("/api/uploads")
def uploads():
    with LOCK:
        return jsonify(_read_catalog())


@app.get("/api/catalog-file")
def catalog_file():
    with LOCK:
        if not CATALOG.exists():
            _write_catalog({"provider": LIBRARY_PROVIDER, "uploads": []})
        text = CATALOG.read_text(encoding="utf-8")
    return app.response_class(text, mimetype="application/json")


@app.post("/api/upload")
def upload():
    visibility = (request.form.get("visibility") or "").strip().lower()
    if visibility not in ("public", "private"):
        return jsonify({"error": "Choose public or private."}), 400
    upload_file = request.files.get("file")
    if upload_file is None or not upload_file.filename:
        return jsonify({"error": "Choose a file to upload."}), 400

    gaps = public_missing() if visibility == "public" else private_missing()
    if gaps:
        return jsonify({"error": "Missing settings: " + ", ".join(gaps)}), 400

    data = upload_file.read()
    filename = _safe_name(upload_file.filename)
    content_type = _content_type(filename, upload_file.mimetype)
    key = f"{visibility}/{uuid.uuid4().hex}_{filename}"
    try:
        storage = _storage(visibility)
        result = storage.upload(
            key=key,
            file=data,
            content_type=content_type,
            visibility=visibility,
            metadata={"original_name": filename, "visibility": visibility},
        )
        read_back = storage.get_buffer(result.key)
    except StoreBridgeError as exc:
        return _library_error(exc)

    uploaded_at = datetime.now(timezone.utc).isoformat()
    record = {
        "id": uuid.uuid4().hex,
        "provider": result.provider,
        "visibility": visibility,
        "key": result.key,
        "filename": filename,
        "content_type": result.content_type or content_type,
        "size": result.size if result.size is not None else len(data),
        "etag": result.etag,
        "url": result.url,
        "uploaded_at": uploaded_at,
        "read_back_size": len(read_back),
        "read_back_matches": read_back == data,
        **_embed(read_back, content_type),
    }
    with LOCK:
        catalog = _read_catalog()
        catalog["provider"] = LIBRARY_PROVIDER
        catalog["updated_at"] = uploaded_at
        catalog.setdefault("uploads", []).insert(0, record)
        _write_catalog(catalog)
    return jsonify({"record": record, "catalog": "data/uploads.json"})


@app.get("/api/read")
def read():
    visibility = (request.args.get("visibility") or "").strip().lower()
    key = (request.args.get("key") or "").strip()
    if visibility not in ("public", "private") or not key:
        return jsonify({"error": "key and visibility are required."}), 400
    try:
        storage = _storage(visibility)
        data = storage.get_buffer(key)
        meta = storage.get_metadata(key)
        url = None
        url_error = None
        try:
            if visibility == "public":
                url = storage.get_url(key)
            else:
                url = storage.get_signed_url(key, expires_in=900, method="GET")
        except StoreBridgeError as exc:
            url_error = str(exc)
    except StoreBridgeError as exc:
        return _library_error(exc)

    return jsonify(
        {
            "key": key,
            "visibility": visibility,
            "url": url,
            "url_error": url_error,
            "size": meta.size,
            "content_type": meta.content_type,
            "etag": meta.etag,
            "read_back_size": len(data),
            **_embed(data, meta.content_type or "application/octet-stream"),
        }
    )


@app.get("/api/download")
def download():
    visibility = (request.args.get("visibility") or "").strip().lower()
    key = (request.args.get("key") or "").strip()
    if visibility not in ("public", "private") or not key:
        return jsonify({"error": "key and visibility are required."}), 400
    try:
        storage = _storage(visibility)
        data = storage.get_buffer(key)
        meta = storage.get_metadata(key)
    except StoreBridgeError as exc:
        return _library_error(exc)
    inline = (request.args.get("inline") or "").strip() == "1"
    return send_file(
        io.BytesIO(data),
        mimetype=meta.content_type or "application/octet-stream",
        as_attachment=not inline,
        download_name=Path(key).name,
    )


@app.post("/api/migrate")
def migrate_route():
    body = request.get_json(silent=True) or {}
    target = str(body.get("to") or "").strip().lower()
    visibility = str(body.get("visibility") or "public").strip().lower()
    source_key = str(body.get("source_key") or "").strip() or None
    dry_run = bool(body.get("dry_run", True))
    skip_existing = bool(body.get("skip_existing", True))
    delete_source = bool(body.get("delete_source", False))

    allowed = {item["id"] for item in MIGRATION_TARGETS}
    if target not in allowed and target != "all":
        return jsonify({"error": "Unknown migration target."}), 400
    if visibility not in ("public", "private", "both"):
        return jsonify({"error": "visibility must be public, private, or both."}), 400

    targets = [item["id"] for item in MIGRATION_TARGETS] if target == "all" else [target]
    visibilities = ["public", "private"] if visibility == "both" else [visibility]
    results = []
    try:
        for dest in targets:
            for vis in visibilities:
                result = migrate.run(
                    dest,
                    vis,
                    source_key=source_key,
                    dry_run=dry_run,
                    skip_existing=skip_existing,
                    delete_source=delete_source,
                )
                payload = result.model_dump(mode="json")
                payload["target"] = dest
                payload["visibility"] = vis
                results.append(payload)
    except StoreBridgeError as exc:
        return jsonify({"error": str(exc), "code": getattr(exc, "code", None), "results": results}), 502
    except RuntimeError as exc:
        return jsonify({"error": str(exc), "results": results}), 400
    return jsonify({"results": results})


@app.errorhandler(413)
def too_large(_exc):
    return jsonify({"error": "File is larger than 32 MB."}), 413


@app.errorhandler(HTTPException)
def http_error(exc):
    return jsonify({"error": exc.description}), exc.code


@app.errorhandler(Exception)
def unexpected(exc):
    return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    print(f"{PROVIDER_NAME} test UI: http://127.0.0.1:{PORT}")
    app.run(host="127.0.0.1", port=PORT, debug=False, threaded=True)
