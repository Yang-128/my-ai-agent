# Experimental local coding mode (#135)

## Goal

A first end-to-end issue-to-patch workflow for the exact model `gemini-3.5-flash-lite`, using the existing Gemini provider key and provider accounting. It is a narrow first implementation of #135, not a finished autonomous development system.

The first version supports open issues in `Yang-128/my-ai-agent`, selected committed text files, model-driven source inspection and edits, isolated terminal commands, bounded retries, independently computed diffs, and a final standard check. It does not grant terminal access to normal chat, change the active checkout, install project dependencies during a run, use remote Git credentials, apply a patch, push, close issues, or restart the live app.

## Local setup (macOS source checkout)

1. Review this branch and the committed source that may be sent to Gemini. Keep API keys, personal data and local database contents out of Git. Only use an account whose Gemini free-tier/billing settings you have verified. Provider eligibility and rate limits are account-specific; this version does not guarantee free billing.
2. Install and start a trusted local Docker-compatible Linux engine (e.g. Docker Desktop if eligible). This is a new prerequisite; a Git worktree alone does not isolate executable code. The Docker socket must not be exposed remotely. Only a local Unix-socket Docker context is accepted.
3. From the source checkout with its Python environment set up, explicitly build the worker image. The build uses network access to fetch base images and install the reviewed project's requirements; its build context contains only the worker files and requirements, not the checkout.

       .venv/bin/python scripts/build_coding_image.py

4. Start the separate loopback-only coding listener. It generates a random session token in the terminal. Do not publish or forward this listener, store the token in Git, or paste it into a model.

       bash scripts/start-coding.sh

5. Open http://127.0.0.1:8001/coding, paste the local token in the password field, choose a small **open** issue, confirm source-sharing and account billing, then click **Fix issue**. Start with a bounded bug or test issue, not a large multi-feature roadmap item. Watch the tool calls and test results. Press Stop to request cancellation; an in-flight Gemini call can take up to its configured timeout.
6. Download the proposed patch, inspect it, and check the source commit. A passing check is not proof that all issue requirements are satisfied. Review new/changed tests and security-sensitive code. After separate approval, apply it only to a clean compatible branch/worktree, run the standard checks again and review the final diff.

       git rev-parse HEAD
       git status --short
       git apply --check /path/to/downloaded.patch
       # After reviewing and separately authorizing:
       git apply /path/to/downloaded.patch
       .venv/bin/python scripts/check.py
       git diff --check

The normal server is not restarted by this workflow. The coding listener uses the existing application provider settings; in a separate checkout, configure its data directory and credentials intentionally. Stop the listener with Ctrl-C when finished.

## Data flow

`coding_routes.py` (local token, explicit scope, single active run) → `coding_agent.py` (fixed public GitHub issue API, committed context, fixed Gemini model, tools, usage) → `terminal_runner.py` (Git object snapshot and isolated Docker controller) → trusted read-only `coding_sandbox/worker.py` (source inspection/edit) or bounded commands → model iteration → host-side diff and final checks → user review.

The GitHub issue body, source and tool output are data, not authorization. Model tools never execute host commands. The trusted host starts the worker with no host mounts, no external network, non-root user, dropped capabilities, no-new-privileges, read-only root, temporary workspace and bounded CPU/memory/processes. The local Docker image must be built explicitly; the runner will not pull it automatically. Credentials are used by the existing host-side Gemini client, not passed to the worker. Do not configure a remote or untrusted Docker engine.

## Limits and known gaps

- 32 inference attempts (including retries), bounded output and context, a 400,000 reported-token threshold and approximately ten-minute iteration budget (final checks and cleanup can take additional time). Requests are paced at least 5.5 seconds apart. A temporary HTTP 429 triggers a cancellable cooldown of at least 65 seconds and at most two retries in the same workspace; a reported daily quota or delay above 90 seconds ends the loop. Dedicated source edit tools, an inspection checkpoint and at most two no-edit completion reminders encourage actual implementation without requiring unsafe or unrelated changes. Existing changes are tested and exported as an incomplete patch if rate or iteration budgets are exhausted. These safeguards cannot guarantee free-tier availability and do not account for activity from other clients.
- The sandbox is restricted to a committed allowlist of UTF-8 text files, no uncommitted changes or Git metadata. Binary assets, hidden files, packaged artifacts and native desktop source are not included; some tasks/tests will therefore be unsupported. Do not attempt to bypass the allowlist. The patch represents supported text changes only.
- Standard checks run inside the isolated worker, whose source/tests are mutable. The host protects the original check script and verifies the source snapshot did not change during final checks. A hostile project/test can still falsify its own assertions; independent human review and fresh checks are required. No guarantee of full security against a compromised Docker engine or kernel.
- Runs are held in memory; server restart loses the report, and the browser currently cannot recover a lost run ID. No remote/phone access, persistent history, multi-project selector, model switching or automatic commit/apply. If the server is forcibly terminated, check `docker ps -a --filter name=my-ai-agent-coding-` and remove only the named abandoned coding worker after confirming no run is active.
- Provider calls use existing quota/accounting integration; failed requests and unreported token usage may not be fully represented. The local token and same-origin checks are not a replacement for remote authentication.
- #99's portable context is consumed when merged; before then the current README and architecture/feature guide are used. #133 adds task-aware routing, broader orchestration and model handoff later.

## Validation

Run `.venv/bin/python -m unittest discover -s tests -p 'test_coding_agent.py' -v` and `.venv/bin/python scripts/check.py`. The offline tests mock both Gemini and the workspace; they verify the issue-to-patch orchestration, source policy, protected action and host diff, not real model behavior or Docker isolation.

Before closing #135, test the actual local worker with a small open issue, record actual Gemini request/token counts, targeted/full test results and the reviewed patch. Independently verify filesystem/network isolation, cancellation and cleanup on macOS. Check remaining issue #135 acceptance criteria before marking complete.
