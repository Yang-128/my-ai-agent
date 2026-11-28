# My AI Agent — Developer Documentation

## Documentation index

- [Portable project context](../PROJECT_CONTEXT.md) — goals, architecture, roadmap, development workflow and model handoff
- [AI contributor entry point](../AGENTS.md)

- [Architecture](ARCHITECTURE.md)
- [Repository map](REPOSITORY_MAP.md) — generated
- [Function index](FUNCTION_INDEX.md) — generated
- [Feature code map](FEATURE_CODE_MAP.md) — generated cross-layer implementation references
- [Dependency map](DEPENDENCY_MAP.md) — generated
- [Data flow](DATA_FLOW.md)
- [Feature guide](FEATURE_GUIDE.md)
- [Cleanup audit](CLEANUP_AUDIT.md)
- [Development log](DEVELOPMENT_LOG.md)

## Regenerate indexes

From the repository root:

    .venv/bin/python scripts/generate_code_map.py

The generator uses Python's standard library. Python symbols use AST parsing; JavaScript and Rust use best-effort declaration matching.

## Maintenance

1. Update relevant documentation when behavior or architecture changes.
2. Regenerate indexes after source changes.
3. Review generated diffs before committing.
4. Never document secrets, credentials, databases, pending-action contents, or personal data.
5. Record uncertain cleanup candidates for investigation; do not delete them blindly.
6. Update the portable project context when the baseline or strategic decisions change. Keep detailed status in GitHub issues, and record a sanitized handoff after meaningful development sessions.
7. Use `python3 scripts/export_ai_context.py --help` to prepare a task-specific packet for another model. The exporter reads committed, allowlisted docs only; `--roadmap` and `--issue` opt in to GitHub CLI requests. Review before sharing.
