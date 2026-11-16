import os
import httpx
from pathlib import Path
from runtime import RESOURCE_DIR

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse

from chat import router as chat_router
from storage import (
    init_db,
    create_conversation,
    list_conversations,
    get_conversation,
    get_daily_usage,
    get_turn_usage,
    record_usage,
    get_tool_settings,
    set_tool_enabled
)
from providers import PROVIDERS, get_provider_client, get_provider_model, get_available_models
from tools import get_all_tool_definitions, execute_tool
from calendar_approval import consume_pending_calendar_event, discard_pending_calendar_event, update_pending_calendar_event_times
from gmail_approval import consume_pending_gmail_message, discard_pending_gmail_message, validate_attachments
from google_integrations import (
    start_google_oauth,
    finish_google_oauth,
    google_calendar_status,
    create_calendar_event,
    update_calendar_event,
    delete_calendar_event,
    disconnect_google_calendar,
    google_gmail_status,
    send_gmail_message,
)

import asyncio
import signal
import time
import multiprocessing

app = FastAPI(title="My AI Agent")

_active_browser_tabs = {}
_browser_lock = asyncio.Lock()
_server_started_at = time.monotonic()
_browser_monitor_task = None


@app.post("/api/session/heartbeat")
async def browser_heartbeat(request: Request):
    data = await request.json()
    client_id = data.get("client_id")
    if not isinstance(client_id, str) or not client_id or len(client_id) > 100:
        raise HTTPException(status_code=400, detail="Invalid client ID")

    async with _browser_lock:
        _active_browser_tabs[client_id] = time.monotonic()

    return {"success": True}


async def _monitor_browser_tabs():
    while True:
        await asyncio.sleep(2)
        now = time.monotonic()

        async with _browser_lock:
            for client_id, last_seen in list(_active_browser_tabs.items()):
                if now - last_seen > 10:
                    del _active_browser_tabs[client_id]

            no_tabs = not _active_browser_tabs

        startup_grace_expired = now - _server_started_at > 10

        if no_tabs and startup_grace_expired:
            # Under Uvicorn --reload, terminate the supervisor rather than
            # only killing its worker and leaving the supervisor running.
            parent = multiprocessing.parent_process()
            target_pid = parent.pid if parent is not None else os.getpid()
            os.kill(target_pid, signal.SIGTERM)
            return


@app.on_event("startup")
async def start_browser_monitor():
    global _browser_monitor_task
    _browser_monitor_task = asyncio.create_task(_monitor_browser_tabs())


@app.on_event("shutdown")
async def stop_browser_monitor():
    global _browser_monitor_task
    if _browser_monitor_task:
        _browser_monitor_task.cancel()
        try:
            await _browser_monitor_task
        except asyncio.CancelledError:
            pass
        _browser_monitor_task = None


BASE_DIR = RESOURCE_DIR

init_db()

app.mount(
    "/static",
    StaticFiles(directory=BASE_DIR / "static"),
    name="static"
)

app.include_router(chat_router)


@app.get("/")
async def home():
    html = (BASE_DIR / "static" / "index.html").read_text()
    style_version = (BASE_DIR / "static" / "style.css").stat().st_mtime_ns
    html = html.replace("{{ style_version }}", str(style_version))
    return HTMLResponse(
        content=html,
        headers={"Cache-Control": "no-cache"}
    )


FILE_BROWSER_ROOT = BASE_DIR.resolve()
DOCUMENTS_ROOT = (Path.home() / "Documents" / "My AI Agent").resolve()
DOCUMENTS_ROOT.mkdir(parents=True, exist_ok=True)

FILE_BROWSER_EXCLUDED_DIRS = {
    ".git", ".venv", "__pycache__", "node_modules",
    "target", "dist", ".pytest_cache", ".mypy_cache",
    ".ruff_cache",
}
FILE_BROWSER_MAX_SIZE = 1_000_000


def resolve_browser_path(relative_path: str = "") -> Path:
    target = (FILE_BROWSER_ROOT / relative_path).resolve()

    if not target.is_relative_to(FILE_BROWSER_ROOT):
        raise HTTPException(status_code=403, detail="Access outside the project directory is not allowed.")

    parts = target.relative_to(FILE_BROWSER_ROOT).parts
    if any(
        part.startswith(".")
        or part in FILE_BROWSER_EXCLUDED_DIRS
        or part.lower().endswith((".db", ".sqlite", ".sqlite3", ".pem", ".key", ".p12", ".pfx"))
        for part in parts
    ):
        raise HTTPException(status_code=403, detail="This file or directory is not available.")

    return target


def resolve_documents_path(relative_path: str = "") -> Path:
    target = (DOCUMENTS_ROOT / relative_path).resolve()
    if not target.is_relative_to(DOCUMENTS_ROOT):
        raise HTTPException(status_code=403, detail="Access outside the documents folder is not allowed.")
    if any(part.startswith(".") for part in target.relative_to(DOCUMENTS_ROOT).parts):
        raise HTTPException(status_code=403, detail="Hidden files are not available.")
    return target


@app.get("/api/documents")
def browse_documents(path: str = ""):
    directory = resolve_documents_path(path)
    if not directory.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory.")

    items = []
    try:
        for entry in directory.iterdir():
            try:
                if entry.is_symlink():
                    continue
                item_path = resolve_documents_path(entry.name if directory == DOCUMENTS_ROOT else str(entry.relative_to(DOCUMENTS_ROOT)))
                if not item_path.exists() or not (item_path.is_dir() or item_path.is_file()):
                    continue
                items.append({
                    "name": item_path.name,
                    "path": item_path.relative_to(DOCUMENTS_ROOT).as_posix(),
                    "type": "directory" if item_path.is_dir() else "file",
                    "size": None if item_path.is_dir() else item_path.stat().st_size,
                    "modified": item_path.stat().st_mtime,
                })
            except (OSError, HTTPException):
                continue
    except OSError as error:
        raise HTTPException(status_code=400, detail=str(error))

    items.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))
    current = directory.relative_to(DOCUMENTS_ROOT).as_posix()
    return {
        "path": "" if current == "." else current,
        "parent": str(Path(current).parent).replace(".", "", 1) if current != "." else None,
        "items": items,
    }


@app.get("/api/documents/content")
def read_document_content(path: str):
    target = resolve_documents_path(path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    size = target.stat().st_size
    if size > 5_000_000:
        raise HTTPException(status_code=413, detail="File is too large to preview (maximum 5 MB).")

    try:
        suffix = target.suffix.lower()

        if suffix == ".docx":
            from docx import Document
            document = Document(target)
            content = "\\n".join(
                paragraph.text for paragraph in document.paragraphs
            )
            for table in document.tables:
                content += "\\n\\n" + "\\n".join(
                    " | ".join(cell.text for cell in row.cells)
                    for row in table.rows
                )

        elif suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(target))
            content = "\\n\\n".join(page.extract_text() or "" for page in reader.pages)

        elif suffix == ".xlsx":
            from openpyxl import load_workbook
            workbook = load_workbook(target, read_only=True, data_only=True)
            sections = []
            for sheet in workbook.worksheets:
                rows = [
                    " | ".join("" if value is None else str(value) for value in row)
                    for row in sheet.iter_rows(values_only=True)
                ]
                sections.append(f"[{sheet.title}]\\n" + "\\n".join(rows))
            content = "\\n\\n".join(sections)

        elif suffix == ".pptx":
            from pptx import Presentation
            presentation = Presentation(target)
            slides = []
            for index, slide in enumerate(presentation.slides, 1):
                texts = [
                    shape.text for shape in slide.shapes
                    if getattr(shape, "has_text_frame", False) and shape.text.strip()
                ]
                slides.append(f"[Slide {index}]\\n" + "\\n".join(texts))
            content = "\\n\\n".join(slides)

        else:
            content = target.read_text(encoding="utf-8")
            if "\\x00" in content:
                raise UnicodeError("Binary file")

    except (UnicodeError, OSError, ValueError, ImportError) as error:
        raise HTTPException(status_code=415, detail="This file cannot be previewed: " + str(error))

    return {
        "name": target.name,
        "path": target.relative_to(DOCUMENTS_ROOT).as_posix(),
        "size": size,
        "content": content,
    }


@app.get("/api/files")
def browse_files(path: str = ""):
    directory = resolve_browser_path(path)

    if not directory.is_dir():
        raise HTTPException(status_code=400, detail="Path is not a directory.")

    items = []
    try:
        for entry in directory.iterdir():
            try:
                item_path = resolve_browser_path(str(entry.relative_to(FILE_BROWSER_ROOT)))
                if not item_path.exists():
                    continue

                is_directory = item_path.is_dir()
                if not is_directory and not item_path.is_file():
                    continue

                relative = item_path.relative_to(FILE_BROWSER_ROOT).as_posix()
                items.append({
                    "name": item_path.name,
                    "path": relative,
                    "type": "directory" if is_directory else "file",
                    "size": None if is_directory else item_path.stat().st_size,
                    "modified": item_path.stat().st_mtime,
                })
            except (OSError, HTTPException):
                continue
    except OSError as error:
        raise HTTPException(status_code=400, detail=str(error))

    items.sort(key=lambda item: (item["type"] != "directory", item["name"].lower()))

    current = directory.relative_to(FILE_BROWSER_ROOT).as_posix()
    return {
        "path": "" if current == "." else current,
        "parent": str(Path(current).parent).replace(".", "", 1) if current != "." else None,
        "items": items,
    }


@app.get("/api/files/content")
def read_browser_file(path: str):
    target = resolve_browser_path(path)

    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found.")

    size = target.stat().st_size
    if size > FILE_BROWSER_MAX_SIZE:
        raise HTTPException(status_code=413, detail="File is too large to preview (maximum 1 MB).")

    try:
        content = target.read_text(encoding="utf-8")
        if "\\x00" in content:
            raise UnicodeError("Binary file")
    except (UnicodeError, OSError):
        raise HTTPException(status_code=415, detail="This file cannot be previewed as text.")

    return {
        "name": target.name,
        "path": target.relative_to(FILE_BROWSER_ROOT).as_posix(),
        "size": size,
        "content": content,
    }


@app.get("/health")
async def health():
    return {"status": "online", "app": "My AI Agent"}


@app.get("/api/conversations")
def conversations():
    return list_conversations()


@app.post("/api/conversations")
def new_conversation():
    conversation_id = create_conversation()
    return {"id": conversation_id}


@app.get("/api/conversations/{conversation_id}")
def conversation_detail(conversation_id: str):
    result = get_conversation(conversation_id)

    if not result:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return result


@app.get("/api/providers")
def provider_status():
    return [
        {
            "id": provider_id,
            "name": config["name"],
            "configured": bool(os.getenv(config["api_key"], "").strip()),
            "default_model": config["default_model"],
            "display_model": config.get(
                "display_model", config["default_model"]
            ),
            "models": get_available_models(provider_id)
        }
        for provider_id, config in PROVIDERS.items()
    ]


@app.get("/api/tools")
def tools_status():
    definitions = get_all_tool_definitions()
    settings = get_tool_settings()
    return [
        {
            "name": item["function"]["name"],
            "description": item["function"].get("description", ""),
            "parameters": item["function"].get("parameters", {}),
            "source": "MCP" if item["function"]["name"].startswith("mcp_") else "Built-in",
            "enabled": settings.get(item["function"]["name"], True),
        }
        for item in definitions
    ]




@app.post("/api/tools/{tool_name}/enabled")
def update_tool_enabled(tool_name: str, body: dict):
    available = {
        item["function"]["name"]
        for item in get_all_tool_definitions()
    }
    if tool_name not in available:
        raise HTTPException(status_code=404, detail="Tool not found")

    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(
            status_code=422,
            detail="'enabled' must be true or false"
        )

    set_tool_enabled(tool_name, enabled)
    return {"name": tool_name, "enabled": enabled}


@app.get("/api/tools/status")
def tools_server_status():
    definitions = get_all_tool_definitions()
    mcp_count = sum(
        item["function"]["name"].startswith("mcp_")
        for item in definitions
    )
    return {
        "mcp": {
            "connected": mcp_count > 0,
            "server": "MCP",
            "tool_count": mcp_count,
            "error": None if mcp_count else "No MCP tools discovered"
        }
    }


@app.post("/api/tools/{tool_name}/run")
def run_tool(tool_name: str, arguments: dict):
    if tool_name == "mcp_create_calendar_event":
        raise HTTPException(status_code=403, detail="Calendar events must be approved from the chat proposal.")
    available = {
        item["function"]["name"]
        for item in get_all_tool_definitions()
    }
    if tool_name not in available:
        raise HTTPException(status_code=404, detail="Tool not found")
    if not get_tool_settings().get(tool_name, True):
        raise HTTPException(status_code=403, detail="Tool is disabled")
    try:
        return {"success": True, "result": str(execute_tool(tool_name, arguments))}
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.get("/api/usage")
def daily_usage():
    return {
        "daily": get_daily_usage(),
        "turns": get_turn_usage()
    }

def refresh_openrouter_usage():
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("OPENROUTER_API_KEY is missing")

    response = httpx.get(
        "https://openrouter.ai/api/v1/key",
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=15
    )
    response.raise_for_status()
    return response.json().get("data", {})

@app.post("/api/usage/refresh")
def refresh_usage():
    results = {}

    for provider_id in ("groq", "gemini", "openrouter"):
        try:
            model = get_provider_model(provider_id)

            if provider_id == "openrouter":
                quota = refresh_openrouter_usage().get(
                    "free_model_daily_requests", {}
                )

                record_usage(
                    provider_id,
                    model,
                    requests_limit=quota.get("limit"),
                    requests_remaining=quota.get("remaining")
                )

                results[provider_id] = "refreshed"
                continue

            if provider_id == "gemini":
                results[provider_id] = "local tracking"
                continue

            client = get_provider_client(provider_id)

            response = client.chat.completions.with_raw_response.create(
                model=model,
                messages=[{"role": "user", "content": "Reply OK."}],
                max_tokens=2
            )

            headers = response.headers

            record_usage(
                provider_id,
                model,
                requests_limit=_int_header(headers, "x-ratelimit-limit-requests"),
                requests_remaining=_int_header(headers, "x-ratelimit-remaining-requests"),
                requests_reset=headers.get("x-ratelimit-reset-requests"),
                tokens_limit=_int_header(headers, "x-ratelimit-limit-tokens")
            )

            results[provider_id] = "refreshed"

        except Exception as error:
            results[provider_id] = str(error)

    return {
        "status": "completed",
        "providers": results,
        "usage": get_daily_usage(),
        "turns": get_turn_usage()
    }


def _int_header(headers, name):
    try:
        value = headers.get(name)
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


@app.get("/api/google/calendar/status")
def google_calendar_connection_status():
    return google_calendar_status()


@app.get("/api/google/connect")
def google_calendar_connect():
    try:
        return RedirectResponse(start_google_oauth(), status_code=302)
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))


@app.get("/api/google/callback")
def google_calendar_callback(request: Request):
    if request.query_params.get("error"):
        return HTMLResponse(
            "<h2>Google Calendar connection cancelled or denied.</h2>",
            status_code=400,
        )

    try:
        finish_google_oauth(
            str(request.url),
            request.query_params.get("state"),
        )
        return HTMLResponse("""
            <html><body style="font-family:system-ui;max-width:600px;margin:80px auto">
            <h2>Google account connected</h2>
            <p>You can close this tab and return to My AI Agent.</p>
            </body></html>
        """)
    except Exception as error:
        return HTMLResponse(
            f"<h2>Google connection failed</h2><p>{str(error)}</p>",
            status_code=400,
        )


@app.post("/api/google/calendar/approve")
def google_calendar_approve_event(body: dict):
    pending_ids = body.get("pending_ids")
    if pending_ids is None:
        pending_id = str(body.get("pending_id", "")).strip()
        pending_ids = [pending_id] if pending_id else []

    approved = body.get("approved")

    if (
        not isinstance(pending_ids, list)
        or not pending_ids
        or not all(isinstance(item, str) and item.strip() for item in pending_ids)
        or len(pending_ids) > 50
        or not isinstance(approved, bool)
    ):
        raise HTTPException(
            status_code=422,
            detail="Pending event IDs and an approval decision are required.",
        )

    if not approved:
        declined = sum(
            1 for pending_id in pending_ids
            if discard_pending_calendar_event(pending_id)
        )
        if declined == 0:
            raise HTTPException(
                status_code=404,
                detail="This proposal has expired or was already handled.",
            )
        return {
            "success": True,
            "approved": False,
            "declined": declined,
            "message": f"{declined} calendar change(s) declined.",
        }

    supplied_times = body.get("times", [])
    if supplied_times is not None and (
        not isinstance(supplied_times, list) or len(supplied_times) != len(pending_ids)
    ):
        raise HTTPException(status_code=422, detail="Calendar time entries must match the pending proposals.")

    if supplied_times:
        for pending_id, time_entry in zip(pending_ids, supplied_times):
            if not isinstance(time_entry, dict):
                raise HTTPException(status_code=422, detail="Invalid calendar time entry.")
            try:
                update_pending_calendar_event_times(
                    pending_id,
                    time_entry.get("start_time", ""),
                    time_entry.get("end_time", ""),
                    time_entry.get("start_date", ""),
                    time_entry.get("end_date", ""),
                )
            except ValueError as error:
                raise HTTPException(status_code=422, detail=str(error))

    results = []

    for pending_id in pending_ids:
        try:
            pending = consume_pending_calendar_event(pending_id)
            proposal = pending["proposal"]
            action = proposal["action"]
            event_id = proposal.get("event_id", "")
            event_data = proposal.get("event", {})

            if action == "create":
                event = create_calendar_event(
                    summary=event_data["summary"],
                    start_time=event_data.get("start", {}).get("dateTime", ""),
                    end_time=event_data.get("end", {}).get("dateTime", ""),
                    start=event_data.get("start"),
                    end=event_data.get("end"),
                    recurrence=event_data.get("recurrence"),
                    location=event_data.get("location", ""),
                    description=event_data.get("description", ""),
                )
                results.append({
                    "success": True,
                    "action": action,
                    "event_id": event.get("id"),
                    "html_link": event.get("htmlLink"),
                    "summary": event.get("summary"),
                })

            elif action == "update":
                event = update_calendar_event(event_id, event_data)
                results.append({
                    "success": True,
                    "action": action,
                    "event_id": event.get("id"),
                    "html_link": event.get("htmlLink"),
                    "summary": event.get("summary"),
                })

            elif action == "delete":
                delete_calendar_event(event_id)
                results.append({
                    "success": True,
                    "action": action,
                    "event_id": event_id,
                    "summary": event_data.get("summary", ""),
                })

            else:
                raise ValueError("Unsupported calendar action.")

        except Exception as error:
            results.append({
                "success": False,
                "error": str(error),
                "pending_id": pending_id,
            })

    succeeded = sum(1 for item in results if item["success"])
    return {
        "success": succeeded == len(results),
        "approved": True,
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }


@app.get("/api/google/gmail/status")
def google_gmail_connection_status():
    return google_gmail_status()


@app.get("/api/google/gmail/attachments")
def google_gmail_available_attachments():
    root = (Path.home() / "Documents" / "My AI Agent").resolve()
    allowed = {".xlsx", ".xls", ".csv", ".pdf", ".docx", ".doc", ".pptx", ".txt", ".md", ".html", ".png", ".jpg", ".jpeg", ".webp"}
    items = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [name for name in dirnames if not name.startswith(".") and not (Path(directory) / name).is_symlink()]
        for filename in filenames:
            target = Path(directory) / filename
            try:
                if target.is_symlink() or target.suffix.lower() not in allowed:
                    continue
                resolved = target.resolve()
                if not resolved.is_relative_to(root) or not resolved.is_file():
                    continue
                size = resolved.stat().st_size
                if size <= 20 * 1024 * 1024:
                    items.append({
                        "name": resolved.name,
                        "path": resolved.relative_to(root).as_posix(),
                        "size": size,
                    })
            except OSError:
                continue
    items.sort(key=lambda item: item["path"].lower())
    return {"items": items}


@app.post("/api/google/gmail/approve")
def google_gmail_approve_message(body: dict):
    pending_id = str(body.get("pending_id", "")).strip()
    approved = body.get("approved")

    if not pending_id or not isinstance(approved, bool):
        raise HTTPException(status_code=422, detail="A pending email ID and approval decision are required.")

    if not approved:
        if not discard_pending_gmail_message(pending_id):
            raise HTTPException(status_code=404, detail="This email proposal has expired or was already handled.")
        return {"success": True, "approved": False, "message": "Email declined."}

    try:
        pending = consume_pending_gmail_message(pending_id)
        message = pending["message"]
        edits = body.get("message")
        if edits is not None:
            if not isinstance(edits, dict):
                raise ValueError("Edited email details must be an object.")
            for field in ("to", "cc", "bcc", "reply_to", "subject", "body"):
                if field in edits:
                    message[field] = str(edits[field] or "").strip() if field != "body" else str(edits[field] or "")
            if "attachments" in edits:
                message["attachments"] = validate_attachments(edits["attachments"])

        message["attachments"] = validate_attachments(message.get("attachments", []))
        if not message["to"] or not message["subject"] or not message["body"].strip():
            raise ValueError("To, Subject, and Body are required.")
        if len(message["to"]) > 2000 or len(message["cc"]) > 2000 or len(message["bcc"]) > 2000:
            raise ValueError("Recipient fields are too long.")
        if len(message["subject"]) > 998 or len(message["body"]) > 50000:
            raise ValueError("The email exceeds the allowed size.")

        result = send_gmail_message(**message)
        return {
            "success": True,
            "approved": True,
            "message_id": result.get("id", ""),
            "thread_id": result.get("threadId", ""),
            "recipient": message["to"],
            "subject": message["subject"],
            "attachment_count": len(message["attachments"]),
        }
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error))

@app.post("/api/google/calendar/disconnect")
def google_calendar_disconnect():
    disconnect_google_calendar()
    return {"connected": False}
