"""Local, explicit opt-in coding screen; never register terminal tools in ordinary chat."""
import hmac
import os
import threading
import uuid
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from coding_agent import MODEL, run_issue
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


class StartRequest(BaseModel):
    issue: int = Field(ge=1, le=1_000_000)
    approved: bool = False


@router.get("/coding")
def coding_page(request: Request):
    local_request(request, token=False)
    return FileResponse(RESOURCE_DIR / "static" / "coding.html", headers={
        "Cache-Control": "no-store",
        "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; form-action 'none'; base-uri 'none'; frame-ancestors 'none'",
        "Referrer-Policy": "no-referrer",
    })


def run_worker(run_id, number, stop):
    def emit(kind, payload):
        with LOCK:
            run = RUNS[run_id]
            run["events"].append({"kind": kind, "data": payload})
            if len(run["events"]) > 180:
                run["events"] = run["events"][-180:]

    try:
        result = run_issue(number, stop, emit)
    except CodingCancelled:
        result = {"status": "cancelled", "message": "Coding run cancelled; no patch applied."}
    except CodingError as exc:
        result = {"status": "error", "message": str(exc)}
    except Exception as exc:
        # Never return raw SDK exceptions (may contain request data or credentials).
        result = {"status": "error", "message": f"Coding run failed ({type(exc).__name__}). "
                  "Check Docker setup, configured Gemini account, API availability and quota."}
    with LOCK:
        RUNS[run_id]["result"] = result
        RUNS[run_id]["status"] = result["status"]


@router.post("/api/coding/start")
def start(request: Request, body: StartRequest):
    local_request(request)
    if not body.approved:
        raise HTTPException(422, "Explicit source-sharing and coding approval required.")
    with LOCK:
        if any(run["status"] == "running" for run in RUNS.values()):
            raise HTTPException(409, "A coding run is already active.")
        while len(RUNS) >= MAX_RUNS:
            RUNS.pop(next(iter(RUNS)))
        run_id = uuid.uuid4().hex
        stop = threading.Event()
        RUNS[run_id] = {"status": "running", "issue": body.issue, "events": [],
                        "stop": stop, "result": None}
        threading.Thread(target=run_worker, args=(run_id, body.issue, stop), daemon=True).start()
    return {"run_id": run_id, "model": MODEL}


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
