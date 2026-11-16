#!/bin/bash
cd "$(dirname "$0")/.."

if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
    open http://127.0.0.1:8000
    exit 0
fi

./.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 &
SERVER_PID=$!

cleanup() {
    kill -TERM "$SERVER_PID" 2>/dev/null
    wait "$SERVER_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

for i in {1..50}; do
    if curl -fsS http://127.0.0.1:8000 >/dev/null 2>&1; then
        open http://127.0.0.1:8000
        break
    fi
    sleep 0.2
done

wait "$SERVER_PID"
