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
