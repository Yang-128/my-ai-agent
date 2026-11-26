# Dependency Map

> Generated from source imports and declared dependency manifests. Static inventory only; it does not represent runtime call relationships.

## Local Python module imports

### `agent.py`

- No imports of other local Python modules detected.

### `app.py`

- `calendar_approval`
- `chat`
- `gmail_approval`
- `google_integrations`
- `provider_ranking`
- `providers`
- `runtime`
- `storage`
- `task_approval`
- `tools`

### `backend_launcher.py`

- `app`
- `mcp_server`

### `calendar_approval.py`

- `runtime`

### `chat.py`

- `calendar_approval`
- `gmail_approval`
- `providers`
- `school_timetable`
- `storage`
- `task_approval`
- `tools`

### `gmail_approval.py`

- `runtime`

### `google_integrations.py`

- `runtime`

### `mcp_client.py`

- `runtime`

### `mcp_server.py`

- `google_integrations`
- `storage`
- `web_search`

### `provider_ranking.py`

- `providers`
- `storage`

### `providers.py`

- `runtime`

### `runtime.py`

- No imports of other local Python modules detected.

### `school_timetable.py`

- No imports of other local Python modules detected.

### `scripts/check.py`

- No imports of other local Python modules detected.

### `scripts/generate_code_map.py`

- No imports of other local Python modules detected.

### `storage.py`

- `runtime`

### `task_approval.py`

- `google_integrations`
- `runtime`

### `test_mcp.py`

- `mcp_client`

### `tests/test_calendar_approval.py`

- `calendar_approval`

### `tests/test_capabilities.py`

- `app`
- `provider_ranking`
- `providers`
- `storage`

### `tests/test_document_generation.py`

- `mcp_client`

### `tests/test_google_approvals.py`

- `app`
- `calendar_approval`
- `task_approval`

### `tests/test_school_timetable.py`

- `calendar_approval`
- `school_timetable`

### `tests/test_storage.py`

- `storage`

### `tools.py`

- `mcp_client`

### `web_search.py`

- No imports of other local Python modules detected.

## JavaScript imports

### `static/app.js`

- No static ES module imports or CommonJS `require()` calls detected.

### `static/service-worker.js`

- No static ES module imports or CommonJS `require()` calls detected.

## Frontend CDN assets

- `https://aistudio.google.com/apikey`
- `https://cdn.jsdelivr.net/npm/dompurify/dist/purify.min.js`
- `https://cdn.jsdelivr.net/npm/marked/marked.min.js`
- `https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js`
- `https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1/highlight.min.js`
- `https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.11.1/styles/github-dark.min.css`
- `https://console.groq.com/keys`
- `https://openrouter.ai/settings/keys`

## Rust crate imports

### `src-tauri/build.rs`

- No top-level `use` declarations detected.

### `src-tauri/src/lib.rs`

- `std`
- `tauri`
- `tauri_plugin_deep_link`

### `src-tauri/src/main.rs`

- No top-level `use` declarations detected.

## Declared external dependencies

### Python

- `fastapi==0.162.7`
- `uvicorn==0.54.0`
- `python-dotenv==1.2.4`
- `google-auth-oauthlib==1.5.0`
- `google-api-python-client==2.202.0`
- `openai==3.51.0`
- `mcp==1.30.0`
- `anyio==4.16.0`
- `pydantic==2.14.3`
- `python-multipart==0.0.33`
- `ddgs==9.16.1`
- `httpx==0.28.1`
- `lxml==6.1.3`
- `pypdf==7.1.0`
- `reportlab==5.1.0`

### JavaScript

- `@tauri-apps/cli ^2.12.4 (devDependencies)`

### Rust

- `serde_json 1.0 (dependencies)`
- `serde 1.0 (dependencies)`
- `log 0.4 (dependencies)`
- `tauri 2.12.5 (dependencies)`
- `tauri-plugin-log 2 (dependencies)`
- `tauri-plugin-deep-link 2 (dependencies)`
- `tauri-build 2.7.4 (build-dependencies)`

## Runtime call relationships

See [Architecture](ARCHITECTURE.md) and [Data Flow](DATA_FLOW.md). This generated file deliberately does not infer runtime calls from imports.
