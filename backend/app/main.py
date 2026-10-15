from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
import uuid
import zipfile
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Generator
from urllib.error import HTTPError, URLError
from urllib.request import Request as UrlRequest, urlopen
from urllib.parse import urlsplit

import pymupdf
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import Image, UnidentifiedImageError
from starlette.background import BackgroundTask

from .engine import STANDALONE_IMAGE_EXTENSIONS, run

BASE = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("DATA_DIR", str(BASE / "data"))).resolve()
DATA.mkdir(parents=True, exist_ok=True)

MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(25 * 1024 * 1024)))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(40 * 1024 * 1024)))
MAX_PDF_PAGES = int(os.getenv("MAX_PDF_PAGES", "1000"))
MAX_PDF_PAGE_PIXELS = int(os.getenv("MAX_PDF_PAGE_PIXELS", "40000000"))
MAX_IMAGE_PIXELS = int(os.getenv("MAX_IMAGE_PIXELS", "40000000"))
MAX_ARCHIVE_BYTES = int(os.getenv("MAX_ARCHIVE_BYTES", str(250 * 1024 * 1024)))
MAX_ARCHIVE_MEMBERS = int(os.getenv("MAX_ARCHIVE_MEMBERS", "10000"))
MAX_WORKERS = max(1, int(os.getenv("MAX_WORKERS", "2")))
MAX_QUEUED_JOBS = max(0, int(os.getenv("MAX_QUEUED_JOBS", "8")))
RETENTION_DAYS = max(1, int(os.getenv("JOB_RETENTION_DAYS", "30")))
AUDIT_RETENTION_DAYS = max(30, int(os.getenv("AUDIT_RETENTION_DAYS", "365")))
MAX_JOB_ATTEMPTS = max(1, int(os.getenv("MAX_JOB_ATTEMPTS", "3")))
MAX_ASSETS_PER_SOURCE = int(os.getenv("MAX_ASSETS_PER_SOURCE", "6000"))
MAX_WORKSHEET_ROWS = int(os.getenv("MAX_WORKSHEET_ROWS", "250000"))
MAX_WORKSHEET_COLUMNS = int(os.getenv("MAX_WORKSHEET_COLUMNS", "256"))
ALLOWED = {".pdf", ".xlsx", ".xlsm"} | STANDALONE_IMAGE_EXTENSIONS
TERMINAL_STATUSES = {"completed", "error", "cancelled"}
APEXIVE_API_URL = os.getenv("APEXIVE_API_URL", "http://127.0.0.1:8099").rstrip("/")
APEXIVE_AUTH_TIMEOUT = float(os.getenv("APEXIVE_AUTH_TIMEOUT", "5"))
APEXIVE_AUTH_RECHECK_SECONDS = max(
    5.0, float(os.getenv("APEXIVE_AUTH_RECHECK_SECONDS", "30"))
)
_auth_endpoint = urlsplit(APEXIVE_API_URL)
if _auth_endpoint.scheme != "https" and _auth_endpoint.hostname not in {
    "localhost",
    "127.0.0.1",
    "::1",
}:
    raise RuntimeError("APEXIVE_API_URL must use HTTPS outside local development.")
if _auth_endpoint.scheme not in {"http", "https"} or not _auth_endpoint.hostname:
    raise RuntimeError("APEXIVE_API_URL must be an absolute HTTP(S) URL.")

logger = logging.getLogger("apexive.trademark")


@asynccontextmanager
async def lifespan(_: FastAPI):
    recover_and_clean_jobs()
    try:
        yield
    finally:
        stop_retention_cleanup()


app = FastAPI(title="Apexive AI Trademark Visual Conflict API", version="5.0.0", lifespan=lifespan)
origins = [
    origin.strip()
    for origin in os.getenv(
        "CORS_ORIGINS",
        "https://trademark-conflict-detector.vercel.app,https://trademark.apexiveai.com,http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

JOBS: dict[str, dict[str, Any]] = {}
_job_lock = threading.RLock()
_cancel_events: dict[str, threading.Event] = {}
_futures: dict[str, Future[Any]] = {}
_job_authorization_tokens: dict[str, str] = {}
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="trademark-job")
_capacity = threading.BoundedSemaphore(MAX_WORKERS + MAX_QUEUED_JOBS)
_cleanup_stop = threading.Event()
_cleanup_thread: threading.Thread | None = None


@contextmanager
def _db() -> Generator[sqlite3.Connection, None, None]:
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA / "jobs.sqlite3", timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute(
        """CREATE TABLE IF NOT EXISTS jobs (
            job_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            status TEXT NOT NULL,
            state_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS audit_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT,
            tenant_id TEXT NOT NULL,
            event TEXT NOT NULL,
            details_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        )"""
    )
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(tenant_id: str, event: str, job_id: str | None = None, **details: Any) -> None:
    with _db() as connection:
        connection.execute(
            "INSERT INTO audit_events(job_id, tenant_id, event, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (job_id, tenant_id, event, json.dumps(details, ensure_ascii=False), _now()),
        )


def _save_job(job_id: str, tenant_id: str, state: dict[str, Any], event: str | None = None) -> None:
    now = _now()
    with _job_lock:
        JOBS[job_id] = dict(state)
    with _db() as connection:
        previous = connection.execute(
            "SELECT created_at FROM jobs WHERE job_id = ? AND tenant_id = ?", (job_id, tenant_id)
        ).fetchone()
        created_at = previous["created_at"] if previous else now
        connection.execute(
            """INSERT INTO jobs(job_id, tenant_id, status, state_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(job_id) DO UPDATE SET status=excluded.status,
                 state_json=excluded.state_json, updated_at=excluded.updated_at
               WHERE jobs.tenant_id=excluded.tenant_id""",
            (job_id, tenant_id, str(state.get("status", "queued")), json.dumps(state), created_at, now),
        )
    if event:
        _audit(tenant_id, event, job_id, status=state.get("status"))


def _tenant(request: Request | None) -> str:
    if request is None:
        raise HTTPException(401, "A verified product session is required.")
    tenant_id = getattr(request.state, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(401, "A verified product session is required.")
    return str(tenant_id)


def _owned_job(job_id: str, tenant_id: str) -> dict[str, Any]:
    with _job_lock:
        cached = JOBS.get(job_id)
    if cached is not None:
        owner = cached.get("_tenant_id", "local-dev")
        if owner != tenant_id:
            raise HTTPException(404, "Job not found")
        return cached
    with _db() as connection:
        row = connection.execute(
            "SELECT tenant_id, state_json FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    if row is None or row["tenant_id"] != tenant_id:
        raise HTTPException(404, "Job not found")
    state = json.loads(row["state_json"])
    with _job_lock:
        JOBS[job_id] = state
    return state


def _public_state(job_id: str, state: dict[str, Any]) -> dict[str, Any]:
    return {"job_id": job_id, **{key: value for key, value in state.items() if not key.startswith("_")}}


@app.middleware("http")
async def authenticate_api(request: Request, call_next):
    is_launch_exchange = request.url.path == "/api/auth/exchange" and request.method == "POST"
    if request.url.path.startswith("/api/") and request.method != "OPTIONS" and not is_launch_exchange:
        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse(
                {"detail": "Bearer authentication is required."},
                status_code=401,
            )
        try:
            access_status, access_body = await asyncio.to_thread(
                _fetch_apexive_product_access,
                token,
            )
        except (OSError, TimeoutError, URLError):
            logger.exception("Apexive product authorization service is unavailable")
            return JSONResponse(
                {"detail": "Product authorization service is unavailable."},
                status_code=503,
            )
        if access_status == 401:
            return JSONResponse(
                {"detail": "Invalid or expired Apexive account session."},
                status_code=401,
            )
        if access_status != 200:
            if access_status >= 500:
                return JSONResponse(
                    {"detail": "Product authorization service is unavailable."},
                    status_code=503,
                )
            return JSONResponse(
                {"detail": "This account is not authorized to use Trademark Conflict."},
                status_code=403,
            )
        try:
            access_data = json.loads(access_body)
            tenant_id = str(access_data.get("tenant_id") or "")
        except (json.JSONDecodeError, AttributeError):
            logger.error("Apexive product authorization returned an invalid response")
            return JSONResponse(
                {"detail": "Product authorization service returned an invalid response."},
                status_code=502,
            )
        if access_data.get("has_access") is not True or not tenant_id:
            return JSONResponse(
                {"detail": "This account is not authorized to use Trademark Conflict."},
                status_code=403,
            )
        request.state.tenant_id = tenant_id
        request.state.authorization_token = token
        if request.method == "POST" and request.url.path == "/api/analyze":
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_UPLOAD_BYTES + 64 * 1024:
                        return JSONResponse(
                            {"detail": "Upload request exceeds the configured size limit."},
                            status_code=413,
                        )
                except ValueError:
                    return JSONResponse(
                        {"detail": "Invalid content length."},
                        status_code=400,
                    )
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    if request.url.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "private, no-store")
    return response


@app.post("/api/auth/exchange")
async def exchange_launch_code(request: Request):
    try:
        payload = await request.json()
        code = payload.get("code") if isinstance(payload, dict) else None
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(400, "A product launch code is required.") from None
    if not isinstance(code, str) or not code.strip() or len(code) > 256:
        raise HTTPException(400, "A product launch code is required.")

    exchange_request = UrlRequest(
        f"{APEXIVE_API_URL}/api/subscriptions/launch/exchange",
        data=json.dumps({"code": code}).encode("utf8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(exchange_request, timeout=APEXIVE_AUTH_TIMEOUT) as response:
            status_code, response_body = response.status, response.read()
    except HTTPError as response:
        status_code, response_body = response.code, response.read()
    except (OSError, TimeoutError, URLError):
        logger.exception("Apexive launch-code exchange service is unavailable")
        raise HTTPException(503, "Product authorization service is unavailable.") from None

    try:
        result = json.loads(response_body)
    except json.JSONDecodeError:
        logger.error("Apexive launch-code exchange returned an invalid response")
        raise HTTPException(502, "Product authorization service returned an invalid response.") from None
    if status_code != 200:
        detail = result.get("detail") if isinstance(result, dict) else None
        if status_code >= 500:
            raise HTTPException(503, "Product authorization service is unavailable.")
        raise HTTPException(status_code, detail or "Product launch link is invalid or expired.")
    if not isinstance(result, dict) or result.get("product_key") != "trademark" or not result.get("access_token"):
        logger.error("Apexive launch-code exchange returned an invalid product session")
        raise HTTPException(502, "Product authorization service returned an invalid session.")
    return result


@app.get("/api/auth/session")
def current_product_session(request: Request):
    return {"tenant_id": request.state.tenant_id, "product_key": "trademark"}


def _fetch_apexive_product_access(token: str) -> tuple[int, bytes]:
    authorization_request = UrlRequest(
        f"{APEXIVE_API_URL}/api/subscriptions/access/trademark",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urlopen(authorization_request, timeout=APEXIVE_AUTH_TIMEOUT) as response:
            return response.status, response.read()
    except HTTPError as response:
        return response.code, response.read()


class ProductAuthorizationError(Exception):
    pass


def _verify_deferred_job_access(token: str, tenant_id: str) -> None:
    try:
        response_status, response_body = _fetch_apexive_product_access(token)
        access_data = json.loads(response_body)
    except (OSError, TimeoutError, URLError, json.JSONDecodeError) as exc:
        raise ProductAuthorizationError(
            "Product authorization could not be rechecked; retry after service recovery."
        ) from exc
    if (
        response_status != 200
        or not isinstance(access_data, dict)
        or access_data.get("has_access") is not True
        or str(access_data.get("tenant_id") or "") != tenant_id
    ):
        raise ProductAuthorizationError(
            "Product access is no longer active; the job was not processed."
        )


def _safe_filename(name: str | None) -> str:
    filename = re.split(r"[\\/]", name or "upload")[-1]
    if filename in {"", ".", ".."} or len(filename) > 255 or any(ord(char) < 32 for char in filename):
        raise HTTPException(400, "Invalid filename.")
    return filename


async def _save_upload(upload: UploadFile, target: Path, total_so_far: int) -> int:
    filename = _safe_filename(upload.filename)
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED:
        raise HTTPException(400, "Each source must be PDF, XLSX, XLSM, or a supported raster logo image.")
    size = 0
    with target.open("wb") as destination:
        while chunk := await upload.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_FILE_BYTES or total_so_far + size > MAX_UPLOAD_BYTES:
                raise HTTPException(413, "Upload exceeds the configured file or total request size limit.")
            destination.write(chunk)
    if size == 0:
        target.unlink(missing_ok=True)
        raise HTTPException(400, "Empty files are not accepted.")
    try:
        _validate_file(target, extension)
    except (
        ValueError, OSError, zipfile.BadZipFile, UnidentifiedImageError,
        Image.DecompressionBombError, pymupdf.FileDataError, RuntimeError,
    ) as exc:
        target.unlink(missing_ok=True)
        raise HTTPException(400, str(exc)) from exc
    return total_so_far + size


def _validate_file(path: Path, extension: str) -> None:
    if extension == ".pdf":
        with path.open("rb") as source:
            if source.read(5) != b"%PDF-":
                raise ValueError("The uploaded PDF does not have a valid PDF signature.")
        try:
            document = pymupdf.open(path)
        except (pymupdf.FileDataError, RuntimeError) as exc:
            raise ValueError("The uploaded PDF is damaged or invalid.") from exc
        try:
            if document.is_encrypted:
                raise ValueError("Password-protected PDFs are not supported.")
            if not 1 <= document.page_count <= MAX_PDF_PAGES:
                raise ValueError(f"PDF page count must be between 1 and {MAX_PDF_PAGES}.")
            for page in document:
                estimated_pixels = page.rect.width * page.rect.height * 2.25
                if estimated_pixels > MAX_PDF_PAGE_PIXELS:
                    raise ValueError(
                        f"PDF page dimensions exceed the configured {MAX_PDF_PAGE_PIXELS:,}-pixel render limit."
                    )
        finally:
            document.close()
        return
    if extension in {".xlsx", ".xlsm"}:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_ARCHIVE_MEMBERS or sum(item.file_size for item in members) > MAX_ARCHIVE_BYTES:
                raise ValueError("Spreadsheet archive exceeds configured expansion limits.")
            if "[Content_Types].xml" not in archive.namelist() or "xl/workbook.xml" not in archive.namelist():
                raise ValueError("The uploaded file is not a valid Excel workbook.")
            if archive.testzip() is not None:
                raise ValueError("The Excel workbook contains a damaged archive member.")
        return
    with Image.open(path) as image:
        width, height = image.size
        if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
            raise ValueError(f"Logo image dimensions exceed the configured {MAX_IMAGE_PIXELS:,}-pixel limit.")
        image.verify()


def _job_dir(job_id: str) -> Path:
    directory = (DATA / job_id).resolve()
    if directory.parent != DATA.resolve():
        raise HTTPException(400, "Invalid job identifier.")
    return directory


class JobCancelled(Exception):
    pass


def _enqueue(job_id: str, tenant_id: str, authorization_token: str) -> None:
    event = threading.Event()
    with _job_lock:
        _cancel_events[job_id] = event
        _job_authorization_tokens[job_id] = authorization_token
        try:
            _futures[job_id] = _executor.submit(
                _run_job, job_id, tenant_id, event, authorization_token
            )
        except Exception:
            _job_authorization_tokens.pop(job_id, None)
            _cancel_events.pop(job_id, None)
            raise


def _run_job(
    job_id: str,
    tenant_id: str,
    cancel_event: threading.Event,
    authorization_token: str,
) -> None:
    directory = _job_dir(job_id)
    try:
        _verify_deferred_job_access(authorization_token, tenant_id)
        state = _owned_job(job_id, tenant_id)
        if cancel_event.is_set():
            state.update(status="cancelled", progress=0, message="Cancelled before processing.")
            _save_job(job_id, tenant_id, state, "cancelled")
            return

        last_authorization_check = time.monotonic()

        def update(progress: int, message: str) -> None:
            nonlocal last_authorization_check
            if cancel_event.is_set():
                raise JobCancelled()
            if time.monotonic() - last_authorization_check >= APEXIVE_AUTH_RECHECK_SECONDS:
                _verify_deferred_job_access(authorization_token, tenant_id)
                last_authorization_check = time.monotonic()
            current = _owned_job(job_id, tenant_id)
            current.update(status="running", progress=max(0, min(99, int(progress))), message=message)
            _save_job(job_id, tenant_id, current)

        input_dir = directory / "input"
        summary = run(directory, input_dir / state["source_a_path"], input_dir / state["source_b_path"], update)
        state = _owned_job(job_id, tenant_id)
        state.update(summary)
        state.update(status="completed", progress=100)
        state["files"] = sorted(
            path.name for path in (directory / "report").iterdir() if path.is_file()
        )
        _save_job(job_id, tenant_id, state, "completed")
    except JobCancelled:
        state = _owned_job(job_id, tenant_id)
        state.update(status="cancelled", message="Analysis cancelled.", progress=state.get("progress", 0))
        _save_job(job_id, tenant_id, state, "cancelled")
    except ProductAuthorizationError as exc:
        state = _owned_job(job_id, tenant_id)
        state.update(status="error", progress=100, message=str(exc))
        _save_job(job_id, tenant_id, state, "authorization_rejected")
    except Exception as exc:
        logger.exception("Analysis job %s failed", job_id)
        try:
            state = _owned_job(job_id, tenant_id)
            state.update(status="error", progress=100, message=str(exc) or "Analysis failed.")
            _save_job(job_id, tenant_id, state, "failed")
        except HTTPException:
            logger.error("Could not persist failure state for job %s", job_id)
    finally:
        with _job_lock:
            _cancel_events.pop(job_id, None)
            _futures.pop(job_id, None)
            _job_authorization_tokens.pop(job_id, None)
        _capacity.release()


def _cleanup_expired() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    audit_cutoff = datetime.now(timezone.utc) - timedelta(days=AUDIT_RETENTION_DAYS)
    with _db() as connection:
        expired = connection.execute(
            "SELECT job_id, tenant_id FROM jobs WHERE status IN ('completed','error','cancelled') AND updated_at < ?",
            (cutoff.isoformat(),),
        ).fetchall()
        for row in expired:
            directory = (DATA / row["job_id"]).resolve()
            if directory.parent == DATA.resolve() and directory.exists():
                shutil.rmtree(directory)
            connection.execute("DELETE FROM jobs WHERE job_id = ?", (row["job_id"],))
            connection.execute(
                "INSERT INTO audit_events(job_id, tenant_id, event, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (row["job_id"], row["tenant_id"], "retention_deleted", "{}", _now()),
            )
            with _job_lock:
                JOBS.pop(row["job_id"], None)
        connection.execute("DELETE FROM audit_events WHERE created_at < ?", (audit_cutoff.isoformat(),))


def _cleanup_loop() -> None:
    while not _cleanup_stop.wait(3600):
        try:
            _cleanup_expired()
        except sqlite3.Error:
            logger.exception("Retention cleanup failed")


def recover_and_clean_jobs() -> None:
    global _cleanup_thread
    with _db() as connection:
        interrupted = connection.execute(
            "SELECT job_id, tenant_id, state_json FROM jobs WHERE status IN ('queued','running','cancelling')"
        ).fetchall()
        for row in interrupted:
            state = json.loads(row["state_json"])
            state.update(status="error", progress=100, message="Worker restarted before this job finished. Retry the job.")
            connection.execute(
                "UPDATE jobs SET status='error', state_json=?, updated_at=? WHERE job_id=?",
                (json.dumps(state), _now(), row["job_id"]),
            )
            connection.execute(
                "INSERT INTO audit_events(job_id, tenant_id, event, details_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (row["job_id"], row["tenant_id"], "worker_restart_recovery", "{}", _now()),
            )
            with _job_lock:
                JOBS.pop(row["job_id"], None)
    _cleanup_expired()
    _cleanup_stop.clear()
    if _cleanup_thread is None or not _cleanup_thread.is_alive():
        _cleanup_thread = threading.Thread(target=_cleanup_loop, name="job-retention", daemon=True)
        _cleanup_thread.start()


def stop_retention_cleanup() -> None:
    _cleanup_stop.set()


@app.get("/")
def root():
    return {"status": "ok"}


@app.get("/health")
def health():
    try:
        with _db() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok", "engine": "v5", "storage": "ok", "workers": MAX_WORKERS}
    except sqlite3.Error as exc:
        logger.exception("Health check failed")
        raise HTTPException(503, "Persistent job storage is unavailable.") from exc


@app.post("/api/analyze", status_code=202)
async def analyze(
    request: Request,
    file_a: UploadFile | None = File(default=None),
    file_b: UploadFile | None = File(default=None),
    source_a: UploadFile | None = File(default=None),
    source_b: UploadFile | None = File(default=None),
    government_pdf: UploadFile | None = File(default=None),
    client_xlsx: UploadFile | None = File(default=None),
):
    tenant_id = _tenant(request)
    upload_a = file_a or source_a or government_pdf
    upload_b = file_b or source_b or client_xlsx
    if upload_a is None or upload_b is None:
        raise HTTPException(400, "Provide both file_a and file_b (or source_a and source_b).")
    if not _capacity.acquire(blocking=False):
        raise HTTPException(429, "Analysis queue is full. Retry when a worker is available.")
    job_id = uuid.uuid4().hex
    directory = _job_dir(job_id)
    total = 0
    try:
        input_dir = directory / "input"
        input_a = input_dir / "A"
        input_b = input_dir / "B"
        input_a.mkdir(parents=True)
        input_b.mkdir(parents=True)
        name_a = _safe_filename(upload_a.filename)
        name_b = _safe_filename(upload_b.filename)
        total = await _save_upload(upload_a, input_a / name_a, total)
        await _save_upload(upload_b, input_b / name_b, total)
        state: dict[str, Any] = {
            "_tenant_id": tenant_id,
            "status": "queued",
            "progress": 0,
            "message": "Queued",
            "source_a": name_a,
            "source_b": name_b,
            "source_a_path": f"A/{name_a}",
            "source_b_path": f"B/{name_b}",
            "source_a_sha256": _sha_file(input_a / name_a),
            "source_b_sha256": _sha_file(input_b / name_b),
            "attempts": 1,
        }
        _save_job(job_id, tenant_id, state, "created")
        _enqueue(job_id, tenant_id, request.state.authorization_token)
        return {"job_id": job_id, "status": "queued"}
    except Exception:
        _capacity.release()
        shutil.rmtree(directory, ignore_errors=True)
        raise


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@app.get("/api/jobs/{jid}")
def job(jid: str, request: Request = None):
    return _public_state(jid, _owned_job(jid, _tenant(request)))


@app.post("/api/jobs/{jid}/retry", status_code=202)
def retry_job(jid: str, request: Request = None):
    tenant_id = _tenant(request)
    state = _owned_job(jid, tenant_id)
    if state.get("status") != "error":
        raise HTTPException(409, "Only failed jobs can be retried.")
    if int(state.get("attempts", 1)) >= MAX_JOB_ATTEMPTS:
        raise HTTPException(409, f"Retry limit reached ({MAX_JOB_ATTEMPTS} attempts).")
    if not _capacity.acquire(blocking=False):
        raise HTTPException(429, "Analysis queue is full. Retry when a worker is available.")
    state.update(status="queued", progress=0, message="Retry queued", attempts=int(state.get("attempts", 1)) + 1)
    _save_job(jid, tenant_id, state, "retry_queued")
    _enqueue(jid, tenant_id, request.state.authorization_token)
    return {"job_id": jid, "status": "queued", "attempts": state["attempts"]}


@app.delete("/api/jobs/{jid}")
def cancel_job(jid: str, request: Request = None):
    tenant_id = _tenant(request)
    state = _owned_job(jid, tenant_id)
    if state.get("status") in TERMINAL_STATUSES:
        raise HTTPException(409, "The job has already finished.")
    with _job_lock:
        cancellation = _cancel_events.get(jid)
        future = _futures.get(jid)
        if cancellation:
            cancellation.set()
        if future and future.cancel():
            state.update(status="cancelled", message="Cancelled before processing.")
            _save_job(jid, tenant_id, state, "cancelled")
            _cancel_events.pop(jid, None)
            _futures.pop(jid, None)
            _job_authorization_tokens.pop(jid, None)
            _capacity.release()
        else:
            state.update(status="cancelling", message="Cancellation requested.")
            _save_job(jid, tenant_id, state, "cancellation_requested")
    return _public_state(jid, state)


@app.get("/api/jobs/{jid}/results")
def results(
    jid: str,
    offset: int = 0,
    limit: int = 100,
    q: str = "",
    decision: str = "",
    request: Request = None,
):
    state = _owned_job(jid, _tenant(request))
    if state.get("status") != "completed":
        raise HTTPException(409, "Results are available after the job completes.")
    path = _job_dir(jid) / "report" / "matches.json"
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    if not path.exists():
        return {"items": [], "total": 0, "offset": offset, "limit": limit}
    items = json.loads(path.read_text(encoding="utf8"))
    query = q.strip().casefold()
    if query:
        searchable_fields = (
            "match_id", "source_a_asset_id", "source_b_asset_id", "source_a_file", "source_b_file",
            "source_a_sheet", "source_b_sheet", "source_a_text", "source_b_text", "application_a", "application_b",
        )
        items = [item for item in items if any(query in str(item.get(field) or "").casefold() for field in searchable_fields)]
    if decision:
        if decision == "EXACT":
            items = [item for item in items if item.get("decision") in {"EXACT_VISUAL_IDENTITY", "EXACT_TEXT_MARK_MATCH"}]
        else:
            items = [item for item in items if item.get("decision") == decision]
    return {"items": items[offset : offset + limit], "total": len(items), "offset": offset, "limit": limit}


@app.get("/api/jobs/{jid}/assets/{side}")
def assets(
    jid: str,
    side: str,
    offset: int = 0,
    limit: int = 100,
    q: str = "",
    request: Request = None,
):
    state = _owned_job(jid, _tenant(request))
    if state.get("status") != "completed":
        raise HTTPException(409, "Extracted assets are available after the job completes.")
    if side not in {"A", "B"}:
        raise HTTPException(400, "Invalid side")
    path = _job_dir(jid) / "report" / f"assets_{side.lower()}.json"
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    if not path.exists():
        return {"items": [], "total": 0, "offset": offset, "limit": limit}
    items = json.loads(path.read_text(encoding="utf8"))
    query = q.strip().casefold()
    if query:
        fields = ("asset_id", "source_file", "application_no", "company", "mark", "mark_text", "sheet", "page", "row", "column", "field_label")
        items = [item for item in items if any(query in str(item.get(field) or "").casefold() for field in fields)]
    total = len(items)
    page_items = items[offset : offset + limit]
    for item in page_items:
        item["asset_url"] = f"/api/jobs/{jid}/assets/{side}/{item['image_file']}" if item.get("image_file") else None
    return {"items": page_items, "total": total, "offset": offset, "limit": limit}


@app.get("/api/jobs/{jid}/assets/{side}/zip")
def asset_zip(jid: str, side: str, request: Request = None):
    _owned_job(jid, _tenant(request))
    if side not in {"A", "B"}:
        raise HTTPException(400, "Invalid side")
    report_path = _job_dir(jid) / "report" / f"assets_{side.lower()}.json"
    if not report_path.exists():
        raise HTTPException(404, "Asset inventory not found")

    items = json.loads(report_path.read_text(encoding="utf8"))
    asset_dir = (_job_dir(jid) / "assets").resolve()
    archive_name = f"source_{side.lower()}_assets.zip"
    zip_path = (DATA / f"{jid}_{side.lower()}_assets.zip").resolve()
    if zip_path.parent != DATA.resolve():
        raise HTTPException(400, "Invalid job identifier.")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in items:
            image_file = item.get("image_file")
            if not image_file:
                continue
            path = (asset_dir / image_file).resolve()
            if path.parent != asset_dir or not path.is_file():
                continue
            archive.write(path, arcname=image_file)

    def cleanup() -> None:
        try:
            if zip_path.exists():
                zip_path.unlink()
        except OSError:
            pass

    _audit(_tenant(request), "asset_zip_exported", jid, side=side, filename=archive_name)
    return FileResponse(
        zip_path,
        filename=archive_name,
        media_type="application/zip",
        headers={"Cache-Control": "private, no-store"},
        background=BackgroundTask(cleanup),
    )


@app.get("/api/jobs/{jid}/assets/{side}/{filename}")
def asset(jid: str, side: str, filename: str, request: Request = None):
    _owned_job(jid, _tenant(request))
    if side not in {"A", "B"}:
        raise HTTPException(400, "Invalid side")
    if Path(filename).name != filename or not re.fullmatch(r"[AB]_\d{6}\.png", filename):
        raise HTTPException(404, "Asset not found")
    path = (_job_dir(jid) / "assets" / filename).resolve()
    if path.parent != (_job_dir(jid) / "assets").resolve() or not path.is_file():
        raise HTTPException(404, "Asset not found")
    _audit(_tenant(request), "asset_downloaded", jid, filename=filename)
    return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, no-store"})


@app.get("/api/jobs/{jid}/files/{filename}")
def output_file(jid: str, filename: str, request: Request = None):
    _owned_job(jid, _tenant(request))
    if Path(filename).name != filename or filename.startswith("."):
        raise HTTPException(404, "File not found")
    path = (_job_dir(jid) / "report" / filename).resolve()
    if path.parent != (_job_dir(jid) / "report").resolve() or not path.is_file():
        raise HTTPException(404, "File not found")
    _audit(_tenant(request), "report_downloaded", jid, filename=filename)
    return FileResponse(path, filename=filename, headers={"Cache-Control": "private, no-store"})


@app.get("/api/audit")
def audit_log(request: Request, limit: int = 100):
    with _db() as connection:
        rows = connection.execute(
            "SELECT job_id, event, details_json, created_at FROM audit_events WHERE tenant_id = ? ORDER BY event_id DESC LIMIT ?",
            (_tenant(request), max(1, min(limit, 500))),
        ).fetchall()
    return {
        "items": [
            {"job_id": row["job_id"], "event": row["event"], "details": json.loads(row["details_json"]), "created_at": row["created_at"]}
            for row in rows
        ]
    }
