# Feature Implementation Guide

## Frontend

| Feature | Main implementation |
|---|---|
| Chat and streaming display | `static/index.html`, `static/app.js`, `static/style.css` |
| Conversation history | `static/app.js`, `app.py`, `storage.py` |
| Image attachments | `static/app.js`, `chat.py` |
| Provider/model selection | `static/app.js`, `providers.py`, `app.py` |
| Tool selection and execution | `static/app.js`, `tools.py`, `mcp_client.py`, `mcp_server.py` |
| Usage view | `static/app.js`, `app.py`, `storage.py` |
| File/document browser | `static/app.js`, `app.py` |
| Google connection and approval UI | `static/app.js`, `app.py` |
| Web app metadata/assets | `static/manifest.webmanifest`, icons, `static/service-worker.js` |

`static/index.html` also loads third-party browser libraries for Markdown, sanitization, syntax highlighting and mathematical typesetting. Check the HTML for exact CDN references.

## Backend APIs

### `app.py`

Owns health/root serving, conversations, provider and tool status, manual tool execution, usage, file/document browsing, and Google connection/status/approval endpoints.

### `chat.py`

- `POST /api/ai-decide-extensions`
- `POST /api/chat`

The chat router validates input, streams provider output, coordinates tools and persists messages and usage.

## Providers

`providers.py` owns provider client and model selection helpers. `chat.py` uses these helpers to construct provider requests. Keep provider capability checks and error handling consistent when adding providers.

## MCP and integrations

- `tools.py`: built-in definitions, MCP definition cache and dispatch.
- `mcp_client.py`: MCP stdio client.
- `mcp_server.py`: MCP tool definitions and implementations.
- `google_integrations.py`: OAuth and Google API operations.
- `web_search.py`: web search and webpage retrieval.

When adding a tool, verify its definition, implementation, dispatch path, user-facing description and approval requirements.

## Debugging Google Calendar proposals

| Symptom | Where to start | What to check |
|---|---|---|
| A calendar request produces a text reply without an approval card | `chat.py`, starting at `ai_decide_extensions` | Follow the selected tools into the chat route. Check that “put these events into my calendar” makes `mcp_create_calendar_events` available. |
| A timetable fails with “Event end time must be later than its start time” | `school_timetable.py`, starting at `weekly_school_actions` | Inspect the local start and end times before `calendar_approval.py` validates them. A school slot of `1157-109` should become 11:57 AM–1:09 PM on the same day. |
| The approval card is missing meetings | `calendar_approval.py` → `create_pending_calendar_actions` → `static/app.js` | Compare the pending proposals with the displayed titles, weekday groups, times, and recurrence rules. The timetable regression expects 11 series covering 18 meetings per week. |
| Deleting a series removes only one occurrence | `mcp_server.py` and `google_integrations.py` for lookup, then `app.py` for approval | Trace the event ID from search to deletion. A whole-series deletion should target the recurring parent. Check a later occurrence after approval. |

Run `./.venv/bin/python -m unittest discover -s tests -p 'test_school_timetable.py' -v` from the repository root. The tests check parsing and pending proposals using temporary storage. Verify browser rendering and live deletion separately.

## Approval modules

- `calendar_approval.py`: pending Calendar action creation, retrieval, updates and discard.
- `gmail_approval.py`: pending Gmail message creation, attachment validation, consume and discard.

Do not bypass approval steps for tools that create, update or send external actions.

## Storage

`storage.py` initializes and accesses SQLite tables for conversations, messages and usage. Preserve compatibility with existing local databases when changing schema or initialization.

## Desktop packaging

- `src-tauri/tauri.conf.json`: product configuration, frontend distribution, development server, backend resource, window, bundle and deep-link configuration.
- `src-tauri/src/lib.rs`: desktop setup and backend process lifecycle.
- `src-tauri/src/main.rs`: Tauri entry point.
- `backend_launcher.py`: frozen backend entry point.
- `backend.spec`: PyInstaller build configuration.
- `dist/backend`: currently tracked packaged backend executable.

Do not rebuild or replace the packaged desktop binary during ordinary web development.

## Tests

`test_mcp.py` is the small existing MCP client test file. The repository does not demonstrate a comprehensive automated test suite. Validate changed routes and flows manually or add focused tests.
