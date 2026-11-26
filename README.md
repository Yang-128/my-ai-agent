# My AI Agent

A Python/FastAPI AI assistant with a browser frontend, provider selection, MCP tools, Google integrations, conversation history, usage tracking, and an optional Tauri desktop application.

## Requirements

- macOS
- Python 3.12 (project virtual environment)
- Node.js and npm for Tauri tooling
- Rust toolchain for desktop builds
- API credentials for whichever AI providers and integrations you use

## Web development setup

Create and activate a virtual environment, then install dependencies:

    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt

Configure provider credentials in the runtime environment file. See `.env.example` for supported variable names. Never commit credentials.

Start the web application:

    ./scripts/start-web.sh

The development server uses `127.0.0.1:8000`.

## Features

- Streaming AI chat with provider and model selection.
- Conversation history and SQLite persistence.
- MCP tool discovery and execution.
- Google Calendar, Google Tasks, and Gmail integrations.
- User approval workflows for Calendar changes and Gmail sending.
- Local file and document browsing.
- Usage tracking.
- Optional Tauri desktop packaging.

Conversation titles use the full opening message and wrap within the history list. Scroll the **Recent** list in the desktop sidebar to open older conversations. On a phone, tap **History** in the bottom navigation.

See [the feature guide](docs/FEATURE_GUIDE.md) for implementation locations.

## Image Attachments and Vision Support

### Application upload limits

Per chat message:

- Maximum 4 images.
- Maximum 6 MB per image.
- Maximum 16 MB combined.
- Supported formats: PNG, JPEG, and WEBP.
- Images are stored with the conversation in SQLite.

There is no application-enforced daily image-upload limit. Providers may impose separate quotas, token limits, image-size limits, and model-specific restrictions.

### Vision behavior

Images are sent directly to the selected model as data URLs in an OpenAI-compatible multimodal message format.

Vision-capable models can interpret image contents alongside the user's text. Text-only models cannot interpret images; the application checks model capability and rejects unsupported image requests.

There is no automatic image-to-text fallback. The application does not first send images to a separate vision service to generate descriptions for a text-only model.

## Files, tools, and free models

**Files** opens My AI Agent Documents. Select a folder to browse it or a file to preview it. **Tools** groups related actions together. Expand a group to enable or disable it, or expand an individual operation to inspect its parameters and try it. The chat picker uses the same groups and respects disabled operations.

The provider picker’s **Find new provider…** checks the Models.dev directory for explicitly free text-chat models with zero listed input and output prices. Built-in providers can be selected directly. External listings link to the provider and require checking its terms.

Each provider card names a representative free model, its practical strengths, and its usage limits. The finder chooses the best model-and-quota combination for each provider. Tool calling, reasoning, image input and long context make up 60% of the ranking; daily allowance makes up 40%, with diminishing returns up to 1,000 requests per day. Where a token cap also applies, the estimate assumes 2,000 tokens per request and uses the tighter limit. Gemini figures are app reference values; check the linked provider page for your account. Providers without a comparable daily cap appear separately, ordered by capabilities.

**Check for free models…** checks the selected provider: Groq’s Free Plan limits, Google’s Gemini free-tier pricing, or OpenRouter’s zero-price chat catalog. For connected Groq and Gemini accounts, results must also appear in the account’s model list. Free-tier eligibility depends on the account plan. Manual model choices outside the verified free list are labeled **check pricing**.

**Default (best available)** automatically resolves to the highest-ranked free model on the selected connected provider. The practical ranking favors tool calling, image input, project recommendations, then context length up to 131,072 tokens and recency where available. Manual selection overrides the default for the session.

Open **Connect an API key** for official provider links and the matching environment variable. Save your key in the server’s local `.env` file and restart the app. Packaged installations use the app’s data directory for this file.

## Testing

After installing the project requirements, run the checks with Node.js 22 on your PATH.

    .venv/bin/python scripts/check.py

GitHub Actions runs the same command on pushes and pull requests. It checks Python and browser JavaScript syntax and runs the unit tests in `tests/` with a temporary application data directory. Add new unit tests in files named `test_*.py`. An empty suite fails the check.

For a live MCP integration smoke test, use the project environment to discover tools and call the current-time tool.

    .venv/bin/python test_mcp.py

## Developer documentation

- [Documentation index](docs/README.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Repository map](docs/REPOSITORY_MAP.md)
- [Function index](docs/FUNCTION_INDEX.md)
- [Dependency map](docs/DEPENDENCY_MAP.md)
- [Data flow](docs/DATA_FLOW.md)
- [Feature guide](docs/FEATURE_GUIDE.md)
- [Cleanup audit](docs/CLEANUP_AUDIT.md)
- [Development log](docs/DEVELOPMENT_LOG.md)

Regenerate the repository indexes after source changes:

    .venv/bin/python scripts/generate_code_map.py

Never commit `.env`, OAuth tokens, local databases, pending-action files, or personal data.
