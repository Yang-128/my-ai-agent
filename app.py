import os
import httpx
from pathlib import Path
from runtime import RESOURCE_DIR

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse

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
from mcp_client import get_mcp_server_status

app = FastAPI(title="My AI Agent")

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
