# Architecture

## Overview

My AI Agent consists of a Python FastAPI backend, a browser frontend, SQLite persistence, an MCP tool server, and an optional Tauri desktop shell.

    Browser UI (static/)
          |
          | HTTP and streamed responses
          v
    FastAPI application (app.py)
          |
          +--- Chat router (chat.py)
          |      +--- Providers (providers.py)
          |      +--- Tool dispatch (tools.py)
          |      +--- Approval modules
          |      +--- Storage (storage.py)
          |
          +--- Google integrations
          +--- File/document browsing
          +--- Usage and tool APIs
          |
          v
    SQLite persistence

    Tool dispatch -> MCP client -> stdio -> MCP server
                                            +-- Google APIs
                                            +-- Filesystem/system tools
                                            +-- Conversation search
                                            +-- Web search/fetch

## Component responsibilities

| Component | Responsibility |
|---|---|
| `static/` | Browser interface, styling, icons, manifest and service-worker asset |
| `app.py` | FastAPI application, REST endpoints, static hosting, file browsing and integration endpoints |
| `chat.py` | Chat validation, provider streaming, tool orchestration and message persistence |
| `providers.py` | Provider clients and model selection |
| `tools.py` | Combines built-in and MCP tool definitions and dispatches calls |
| `mcp_client.py` | Communicates with the MCP server over stdio |
| `mcp_server.py` | Implements MCP tools |
| `storage.py` | SQLite schema and conversation/usage persistence |
| `calendar_approval.py` | Pending Calendar action lifecycle |
| `gmail_approval.py` | Pending Gmail message lifecycle |
| `google_integrations.py` | Google OAuth and Calendar, Tasks and Gmail operations |
| `web_search.py` | Web search and webpage retrieval |
| `runtime.py` | Resource, environment and application-data paths |
| `backend_launcher.py` | Packaged backend and MCP server entry point |
| `src-tauri/` | Desktop lifecycle, configuration, capabilities and icons |
| `dist/backend` | Tracked packaged backend executable |

## Runtime modes

### Web development

`scripts/start-web.sh` starts the web application. Tauri development configuration also starts Uvicorn through `beforeDevCommand`.

### Packaged desktop

`src-tauri/src/lib.rs` manages the backend child process. `src-tauri/tauri.conf.json` packages `dist/backend` as a resource. `backend_launcher.py` starts FastAPI or the MCP server according to its arguments.

`runtime.py` distinguishes source and frozen execution. In frozen mode, the default data directory is under the user's Application Support directory unless `MY_AI_AGENT_DATA_DIR` overrides it.

## Persistence

`storage.py` manages SQLite persistence. The database path is resolved by `runtime.py` and is not necessarily inside the repository.

Calendar and Gmail pending actions are managed by their respective approval modules.

## Security boundaries

- Google API access depends on configured OAuth credentials and tokens.
- Calendar mutations and Gmail sending use application approval workflows.
- Filesystem tools expose local capabilities; review access boundaries before changing them.
- Never commit `.env`, OAuth tokens, databases, pending-action files or user data.
