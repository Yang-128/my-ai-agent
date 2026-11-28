"""Local, explicit opt-in coding screen; never register terminal tools in ordinary chat."""
import hmac
import os
import threading
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from coding_agent import MODEL
from coding_chain import catalog, run_chain, SUPPORTED, MAX_STAGES
from coding_checkpoints import CheckpointStore
from external_development import export_prompt, import_response, saved_patch
from runtime import RESOURCE_DIR
from terminal_runner import CodingError, CodingCancelled

router = APIRouter()
LOCK = threading.Lock()
RUNS = {}
MAX_RUNS = 5


def local_request(request, *, token=True):
    if (not request.client or request.client.host not in {"127.0.0.1", "::1"}
            or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}):
        raise HTTPException(403, "Coding mode is available on the local computer only.")
    if not token:
        return
    secret = os.getenv("MY_AI_AGENT_CODING_TOKEN", "")
    supplied = request.headers.get("x-coding-token", "")
    if len(secret) < 24 or not hmac.compare_digest(secret.encode(), supplied.encode()):
        raise HTTPException(403, "Start the local coding server and enter its session token.")
    if request.method != "GET":
        origin = request.headers.get("origin", "")
        if (origin != f"{request.url.scheme}://{request.url.netloc}"
                or urlsplit(origin).hostname != request.url.hostname):
            raise HTTPException(403, "Same-origin coding requests only.")


class ModelSelection(BaseModel):
    provider: str = Field(min_length=1, max_length=32)
    model: str = Field(min_length=1, max_length=200)


class StartRequest(BaseModel):
    issue: int = Field(ge=1, le=1_000_000)
    approved: bool = False
    models: list[ModelSelection] = Field(default_factory=list, max_length=MAX_STAGES)
    checkpoint_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class ExternalExportRequest(BaseModel):
    issue: int = Field(ge=1, le=1_000_000)
    checkpoint_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    paths: list[str] | None = Field(default=None, max_length=30)
    focus: str = Field(default="", max_length=2000)
    profile: str = Field(default="compact", max_length=32)
    work_scope: str = Field(default="feature", max_length=16)
    approved: bool = False


class ExternalImportRequest(BaseModel):
    issue: int = Field(ge=1, le=1_000_000)
    checkpoint_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    response: str = Field(min_length=1, max_length=450_000)
    provider: str = Field(min_length=1, max_length=60)
    approved: bool = False


def external_operation(request, approved, operation):
    local_request(request)
    if not approved:
        raise HTTPException(422, "Explicit external development approval required.")
    with LOCK:
        if any(run["status"] == "running" for run in RUNS.values()):
            raise HTTPException(409, "Stop the active API coding run first.")
        try:
            return operation()
        except CodingError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(500, f"External development failed ({type(exc).__name__}). Reload saved sessions.") from exc


@router.post("/api/coding/external/export")
def external_export(request: Request, body: ExternalExportRequest):
    return external_operation(request, body.approved,
        lambda: export_prompt(body.issue, body.checkpoint_id, body.paths, focus=body.focus, profile=body.profile, work_scope=body.work_scope))


@router.post("/api/coding/external/import")
def external_import(request: Request, body: ExternalImportRequest):
    return external_operation(request, body.approved,
        lambda: import_response(body.issue, body.checkpoint_id, body.response, body.provider))


@router.get("/coding")
def coding_page(request: Request):
    local_request(request, token=False)
    return FileResponse(RESOURCE_DIR / "static" / "coding.html", headers={
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'",
        "Referrer-Policy": "no-referrer",
    })


def run_worker(run_id, number, stop, models, checkpoint_id):
    def emit(kind, payload):
        with LOCK:
            run = RUNS[run_id]
            run["events"].append({"kind": kind, "data": payload})
            if len(run["events"]) > 180:
                run["events"] = run["events"][-180:]

    try:
        result = run_chain(number, stop, emit, selections=models, checkpoint_id=checkpoint_id)
    except CodingCancelled:
        result = {"status": "cancelled", "message": "Coding run cancelled; no patch applied."}
    except CodingError as exc:
        result = {"status": "error", "message": str(exc)}
    except Exception as exc:
        # Never return raw SDK exceptions (may contain request data or credentials).
        result = {"status": "error", "message": f"Coding run failed ({type(exc).__name__}). "
                  "Check Docker setup, configured provider account, API availability and quota."}
    with LOCK:
        RUNS[run_id]["result"] = result
        RUNS[run_id]["status"] = result["status"]


@router.get("/api/coding/catalog")
def coding_catalog(request: Request):
    local_request(request)
    return catalog()


@router.get("/api/coding/checkpoints")
def coding_checkpoints(request: Request):
    local_request(request)
    return {"checkpoints": CheckpointStore().list()}


@router.get("/api/coding/checkpoints/{identifier}/patch")
def checkpoint_patch(request: Request, identifier: str):
    local_request(request)
    with LOCK:
        try:
            return saved_patch(identifier)
        except CodingError as exc:
            raise HTTPException(422, str(exc)) from exc


@router.delete("/api/coding/checkpoints/{identifier}")
def delete_checkpoint(request: Request, identifier: str):
    local_request(request)
    with LOCK:
        if any(run["status"] == "running" for run in RUNS.values()):
            raise HTTPException(409, "Stop the active coding run first.")
        try:
            CheckpointStore().delete(identifier)
        except CodingError as exc:
            raise HTTPException(422, str(exc)) from exc
    return {"deleted": True}


@router.post("/api/coding/start")
def start(request: Request, body: StartRequest):
    local_request(request)
    if not body.approved:
        raise HTTPException(422, "Explicit source-sharing and coding approval required.")
    models = [m.model_dump() for m in body.models]
    if any(m["provider"] not in SUPPORTED for m in models):
        raise HTTPException(422, "Unsupported coding provider.")
    if len({(m["provider"], m["model"]) for m in models}) != len(models):
        raise HTTPException(422, "Choose distinct models.")
    with LOCK:
        if any(run["status"] == "running" for run in RUNS.values()):
            raise HTTPException(409, "A coding run is already active.")
        while len(RUNS) >= MAX_RUNS:
            RUNS.pop(next(iter(RUNS)))
        run_id = uuid.uuid4().hex
        stop = threading.Event()
        RUNS[run_id] = {"status": "running", "issue": body.issue, "events": [],
                        "stop": stop, "result": None}
        threading.Thread(target=run_worker, args=(run_id, body.issue, stop, models, body.checkpoint_id), daemon=True).start()
    return {"run_id": run_id, "model": models[0]["model"] if models else MODEL}


@router.get("/api/coding/{run_id}")
def get_run(request: Request, run_id: str):
    local_request(request)
    with LOCK:
        run = RUNS.get(run_id)
        if not run:
            raise HTTPException(404, "Coding run not found.")
        return {k: v for k, v in run.items() if k != "stop"}


@router.post("/api/coding/{run_id}/stop")
def stop_run(request: Request, run_id: str):
    local_request(request)
    with LOCK:
        run = RUNS.get(run_id)
        if not run:
            raise HTTPException(404, "Coding run not found.")
        run["stop"].set()
        return {"cancellation_requested": True}
