#!/bin/bash
# Separate local listener; do not stop or restart the normal app.
set -e
cd "$(dirname "$0")/.."
export MY_AI_AGENT_CODING_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf 'Local coding session token (paste into Coding Mode):\n%s\n' "$MY_AI_AGENT_CODING_TOKEN"
printf 'Open http://127.0.0.1:8001/coding after the server starts.\n'
exec .venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 8001
