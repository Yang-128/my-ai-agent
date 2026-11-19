# Cleanup Audit

This is an audit, not a deletion plan. Confirm references and runtime behavior before removing anything.

## Confirmed observations

| Item | Evidence | Recommendation |
|---|---|---|
| `auth.py` | Untracked standalone helper; no integration into FastAPI route handling detected | Keep isolated until an authentication design is approved; do not claim authentication is active |
| `.DS_Store` | Untracked Finder metadata in repository root and `src-tauri/` | Add an ignore rule if desired; remove only after confirmation |
| `static/service-worker.js` | Untracked but served by `app.py` at `/service-worker.js` | Treat as active application code, not an orphan |
| `dist/backend` | Tracked Mach-O arm64 executable referenced by Tauri packaging configuration | Preserve; establish a reproducible release process before changing it |
| `src-tauri/gen/` | Ignored generated Tauri output | Keep generated and out of source inventory |
| `.venv/`, `node_modules/` | Ignored local dependency directories | Keep ignored and recreate from dependency manifests |
| `data-migration-backup-20261116/` | Local backup directory observed | Preserve as user data; contents were not inspected |
| `README.md` | Previously focused mainly on image input behavior | Expand user-facing setup and usage documentation |

## Investigation required

1. **Authentication:** Decide whether authentication is a product requirement. Integrate it consistently if approved; do not merely import `auth.py`.
2. **Packaged backend:** Establish how `dist/backend` is built, tested, versioned and refreshed.
3. **Service worker:** Verify whether service-worker registration and cache cleanup behavior are intentional. The frontend contains cleanup logic while the backend serves `/service-worker.js`.
4. **Pending-action storage:** Review expiration, concurrency and recovery behavior for Calendar and Gmail proposals.
5. **Test coverage:** Add tests for approval flows, storage migrations, tool dispatch and streamed chat error paths.
6. **Potentially unused modules:** Re-check dynamic imports, entry points, packaging and external invocations before declaring any module unused.

## Safety rules

- Never delete secrets, local databases, OAuth credentials, pending actions, migration backups or user data as part of documentation cleanup.
- Never inspect or publish secret contents for inventory purposes.
- Do not remove tracked build artifacts without an explicit build/release plan.
- Re-run tests and inspect `git diff` after cleanup changes.
