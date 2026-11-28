# Contributing to My AI Agent

1. Start with a [GitHub issue](https://github.com/Yang-128/my-ai-agent/issues). Read its parent roadmap issue to understand the intended outcome and use the [developer documentation](docs/README.md) to find the relevant code.
2. Follow the [README](README.md) to set up the local Python and Node environment. Update your local `main` and create a branch for one scoped change.
3. Run `.venv/bin/python scripts/check.py` and verify the affected workflow. For external integrations, state which checks used mocks and which ran against a live service.
4. Open a pull request with the linked issue, a description of the behavior change, any impact on data or permissions, and your test results. Check **Checks** on the pull request for CI results. Link the completed issue from the PR so GitHub can close it on merge.

## Reporting a bug

Include your OS, commit or app version, reproduction steps, and expected and actual behavior. For example, “In Files, I selected a PDF. I expected a text preview. The panel showed an empty result.” Replace real data with synthetic examples. Keep API keys, database files, private conversations, personal identifiers and OAuth tokens out of issues, PRs and logs. For a security concern, use GitHub’s private vulnerability reporting when available, or ask the maintainer privately for a reporting channel before sending details.
