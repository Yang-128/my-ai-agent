# Experimental local coding mode (#135)

## Goal

An issue-to-patch workflow defaulting to `gemini-3.5-flash-lite`, using existing provider credentials and accounting, with explicitly approved free-model handoffs. It is a narrow first implementation of #135, not a finished autonomous development system.

The first version supports open issues in `Yang-128/my-ai-agent`, selected committed text files, model-driven source inspection and edits, isolated terminal commands, bounded retries, independently computed diffs, and a final standard check. It does not grant terminal access to normal chat, change the active checkout, install project dependencies during a run, use remote Git credentials, apply a patch, push, close issues, or restart the live app.

## Local setup (macOS source checkout)

1. Review this branch and the committed source that may be sent to Gemini. Keep API keys, personal data and local database contents out of Git. Only use accounts whose free-tier/billing settings you have verified. Provider eligibility and rate limits are account-specific; this version does not guarantee free billing.
2. Install and start a trusted local Docker-compatible Linux engine (e.g. Docker Desktop if eligible). This is a new prerequisite; a Git worktree alone does not isolate executable code. The Docker socket must not be exposed remotely. Only a local Unix-socket Docker context is accepted.
3. From the source checkout with its Python environment set up, explicitly build the worker image. The build uses network access to fetch base images and install the reviewed project's requirements; its build context contains only the worker files and requirements, not the checkout.

       .venv/bin/python scripts/build_coding_image.py

4. Start the separate loopback-only coding listener. It generates a random session token in the terminal. Do not publish or forward this listener, store the token in Git, or paste it into a model.

       bash scripts/start-coding.sh

5. Open http://127.0.0.1:8001/coding, paste the local token in the password field, choose a small **open** issue, confirm source-sharing and account billing, then click **Fix issue**. Start with a bounded bug or test issue, not a large multi-feature roadmap item. Watch the tool calls and test results. Press Stop to request cancellation; an in-flight provider call can take up to its configured timeout.
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

`coding_routes.py` (local token, explicit scope, single active run) → `coding_agent.py` (fixed public GitHub issue API, committed context, selected model, tools, usage) → `terminal_runner.py` (Git object snapshot and isolated Docker controller) → trusted read-only `coding_sandbox/worker.py` (source inspection/edit) or bounded commands → model iteration → host-side diff and final checks → user review.

The GitHub issue body, source and tool output are data, not authorization. Model tools never execute host commands. The trusted host starts the worker with no host mounts, no external network, non-root user, dropped capabilities, no-new-privileges, read-only root, temporary workspace and bounded CPU/memory/processes. The local Docker image must be built explicitly; the runner will not pull it automatically. Credentials are used by the selected host-side provider client, not passed to the worker. Do not configure a remote or untrusted Docker engine.

## Free-model continuation and saved sessions

After entering the local session token, click **Load free models and saved sessions**. Choose a first model and up to two distinct fallback models in the order you authorize. The catalog uses enabled, credentialed Gemini, Groq and OpenRouter providers, published free listings, tool support and an advertised context of at least 65,536 tokens. OpenRouter entries must also have an explicit `:free` model ID. Discovery and model metadata may be incomplete; unavailable or unverified choices are excluded. Free listings cannot certify an individual account's billing tier. Confirm all selected accounts' billing settings and source-sharing authorization.

Each stage has its own time/request/token budget and records the actual provider/model. Before dispatch, eligibility and locally known quota information are checked again. An incomplete stage hands off to the next authorized eligible model; the new model receives a fresh context with the original issue, recent checkpoint metadata, changed paths and the restored source snapshot. It does not replay previous tool calls. A completed candidate stops the chain for human review. No unselected provider or paid fallback is authorized.

Source snapshots and bounded handoff history are saved atomically under `~/.my-ai-agent/coding-checkpoints`, outside the Git checkout (directory mode 0700, files mode 0600). They are ordinary local files, not encrypted. There are at most twelve saved checkpoints. The screen can reload, resume, or delete saved sessions. Resume requires the same issue and exact original base commit; no automatic rebasing. Checkpoints created with this version survive listener restarts, but older patch-only runs are not retroactively imported. Download important patches before restarting an older listener. Final diffs always compare against the original committed source, including changes across models.

## Issue-directed execution

The runner extracts ordered directions from the issue's latest "Next change" section, or its implementation directions, plus outstanding acceptance criteria. The original issue, current committed source and roadmap context remain available. Workers report each item with concrete evidence using a dedicated progress tool; these reports are model assertions, not independent verification. A passing standard check with unresolved plan items produces a partial-review patch and continues to the next authorized model. Plan reports are checkpointed with each successful source edit and stage boundary. Prior reports are restored only for matching issue text and the current evidence format with passing standard checks. Any later source change or failed check invalidates those reports. Changed issue text requires reassessing coverage against the new directions. Issues without structured directions use the original loop.

## Coding efficiency and evidence

The worker exposes separate schemas for listing, scoped literal search, numbered source reads, exact edits, creation, commands and focused unittest discovery. It accepts only bounded workspace-relative source paths and sandbox commands. A common host interpreter path is normalized to the sandbox Python; no host shell is exposed.

The runner sends small navigation context and retains only the last three complete assistant/tool turns plus current host state as the history grows. Earlier tool results can be reread when necessary. It records actual source changes and rejects a completion report without a cited changed path; acceptance reports additionally require an observed passing test command for the current snapshot. Any subsequent edit or failed check invalidates those claims. These checks constrain model assertions; they do not prove semantic acceptance or replace human review.

On resume, the runner executes up to three changed test files first, carries forward bounded check diagnostics, and avoids repeating a failed full suite on an unchanged checkpoint of the current evidence format. Command output retains the tail, including normal unittest failure tracebacks, rather than only the beginning. A successful edit is saved before the next inference, though interruption during an edit/snapshot itself can still lose that edit. Groq uses max_completion_tokens and provider errors include only allowlisted diagnostic categories, not raw request/response data. Live Groq compatibility still needs verification.

## Limits and known gaps

- 32 inference attempts per stage (including retries), up to three stages, bounded output and context, a 400,000 reported-token threshold and approximately ten-minute iteration budget (final checks and cleanup can take additional time). Requests are paced at least 5.5 seconds apart. A temporary HTTP 429 triggers a cancellable cooldown of at least 65 seconds and at most two retries in the same workspace; a reported daily quota or delay above 90 seconds ends the loop. Dedicated source edit tools, an inspection checkpoint and at most two no-edit completion reminders encourage actual implementation without requiring unsafe or unrelated changes. Existing changes are tested and exported as an incomplete patch if rate or iteration budgets are exhausted. These safeguards cannot guarantee free-tier availability and do not account for activity from other clients.
- The sandbox is restricted to a committed allowlist of UTF-8 text files, no uncommitted changes or Git metadata. Binary assets, hidden files, packaged artifacts and native desktop source are not included; some tasks/tests will therefore be unsupported. Do not attempt to bypass the allowlist. The patch represents supported text changes only.
- Standard checks run inside the isolated worker, whose source/tests are mutable. The host protects the original check script and verifies the source snapshot did not change during final checks. A hostile project/test can still falsify its own assertions; independent human review and fresh checks are required. No guarantee of full security against a compromised Docker engine or kernel.
- Live activity is held in memory; successful edit and stage checkpoints persist locally. No remote/phone access, multi-project selector, or automatic commit/apply. A process crash during an edit or checkpoint can lose work since the preceding saved edit. If the server is forcibly terminated, check `docker ps -a --filter name=my-ai-agent-coding-` and remove only the named abandoned coding worker after confirming no run is active.
- Provider calls use existing quota/accounting integration; failed requests and unreported token usage may not be fully represented. The local token and same-origin checks are not a replacement for remote authentication.
- #99's portable context is consumed when merged; before then the current README and architecture/feature guide are used. #133 adds broader task-aware recommendations and automatic evidence-based ranking later; this MVP uses the user's chosen free-model order.

## Validation

Run `.venv/bin/python -m unittest discover -s tests -p 'test_coding_agent.py' -v` and `.venv/bin/python scripts/check.py`. The offline tests mock providers and the workspace; they verify the issue-to-patch orchestration, source policy, protected action and host diff, not real model behavior or Docker isolation.

Also run `.venv/bin/python -m unittest discover -s tests -p 'test_coding_chain.py' -v` to verify cross-provider handoffs and checkpoint restoration. A real cross-provider run still requires testing.

Before closing #135, test the actual local worker with a small open issue, record actual Gemini request/token counts, targeted/full test results and the reviewed patch. Independently verify filesystem/network isolation, cancellation and cleanup on macOS. Check remaining issue #135 acceptance criteria before marking complete.


## Manual external web development

The coding page also supports a manual workflow with any web conversation model. Enter the local coding token and an open issue number. Use **Load saved sessions only** to resume an existing checkpoint, without contacting API providers. Select the same session when switching web providers; select New coding session only to start independent work.

1. Select the source paths (or leave them blank to use paths referenced in the issue and changed files). Approve prompt preparation, generate the latest prompt, and inspect the exported committed/project-workspace source for sensitive material.
2. Copy the prompt into a web model of your choice, following its terms and free-tier limits. No browser automation, provider API key, or desktop installation is required.
3. Paste the complete JSON object returned by the model, label the developer, and explicitly approve import and isolated testing.
4. The host rejects responses for an older base commit, workspace hash, session ID, or prompt ID. An export invalidates older responses for the same session even if its files did not change. Edits must match existing exported text exactly or create an approved new source file. The protected check runner cannot be changed. Invalid edits are rejected atomically.
5. The candidate is saved locally before offline Docker tests run; failed tests and blocked responses are retained for the next prompt. The next prompt uses the latest saved files and diagnostics, regardless of provider. If a web model is exhausted, simply generate another prompt for the same session and paste it into a different provider.
6. Review the accumulated patch against the recorded base commit. Passing standard tests do not prove that all issue requirements are complete. Nothing is applied, committed, or pushed automatically.

The snapshot is the latest saved version within the chosen session, not an automatic fetch/rebase of GitHub main. A changed checkout commit requires the matching original checkout or a separate new session. Source selection is capped at 30 files and 320 KB per prompt; narrow it if a provider has a smaller context limit. Only manually selected, reviewed code should be shared. Session locking is within the local coding server; run one coding server against the checkpoint directory at a time.
