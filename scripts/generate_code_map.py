#!/usr/bin/env python3
"""Generate repository inventory, symbol index, and dependency map."""
from __future__ import annotations

import ast
import re
import subprocess
import json
import tomllib
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"

EXCLUDED_PARTS = {
    ".git", ".venv", "node_modules", "__pycache__", "target",
    "gen", ".pytest_cache", ".mypy_cache",
}
EXCLUDED_NAMES = {
    ".env", "conversations.db", "google_calendar_token.json",
    "google_oauth_state.json", "pending_calendar_events.json",
    "pending_gmail_messages.json", ".DS_Store",
}

def git_output(*args):
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return result.stdout.splitlines() if result.returncode == 0 else []

def safe_path(value):
    path = Path(value)
    return (
        not any(part in EXCLUDED_PARTS for part in path.parts)
        and path.name not in EXCLUDED_NAMES
        and not path.name.endswith((".pyc", ".sqlite", ".sqlite3"))
    )

def file_purpose(name):
    purposes = {
        ".env.example": "Template for local environment variables.",
        ".gitignore": "Excludes local secrets, data, caches, and generated files.",
        "README.md": "Project overview, setup, usage, and development instructions.",
        "agent.py": "Agent orchestration and execution logic.",
        "app.py": "FastAPI application, HTTP routes, and static hosting.",
        "auth.py": "Standalone authorization helper; not integrated into active routes.",
        "backend.spec": "PyInstaller backend packaging configuration.",
        "backend_launcher.py": "Entry point for backend and MCP server processes.",
        "calendar_approval.py": "Pending Google Calendar actions and approval handling.",
        "chat.py": "Chat API, streaming, provider calls, and tool orchestration.",
        "dist/backend": "Packaged backend executable for desktop distribution.",
        "gmail_approval.py": "Pending Gmail messages and attachment approval handling.",
        "google_integrations.py": "Google OAuth, Calendar, Tasks, and Gmail API operations.",
        "mcp_client.py": "MCP client connection and tool invocation.",
        "mcp_server.py": "MCP server and local integration tool definitions.",
        "package-lock.json": "Locked Node dependency tree.",
        "package.json": "Node package metadata and Tauri CLI dependency.",
        "providers.py": "Provider configuration, model selection, and API clients.",
        "requirements.txt": "Python runtime dependencies.",
        "runtime.py": "Runtime resource and application data path resolution.",
        "scripts/generate_code_map.py": "Generates repository, symbol, and dependency indexes.",
        "scripts/start-web.sh": "Starts the local web development workflow.",
        "src-tauri/.gitignore": "Excludes generated Rust and Tauri build artifacts.",
        "src-tauri/Cargo.lock": "Locked Rust dependency versions.",
        "src-tauri/Cargo.toml": "Rust package metadata and Tauri dependencies.",
        "src-tauri/build.rs": "Tauri build-time configuration.",
        "src-tauri/capabilities/default.json": "Default desktop application capabilities.",
        "src-tauri/src/lib.rs": "Tauri setup, backend process lifecycle, and deep links.",
        "src-tauri/src/main.rs": "Native desktop executable entry point.",
        "src-tauri/tauri.conf.json": "Desktop app, frontend, bundle, and resource configuration.",
        "static/app.js": "Frontend chat, tools, files, usage, and approval interactions.",
        "static/index.html": "Frontend document structure and external UI libraries.",
        "static/manifest.webmanifest": "Web app installation metadata and icons.",
        "static/service-worker.js": "Service worker route and cache lifecycle handling.",
        "static/style.css": "Frontend layout and visual styling.",
        "storage.py": "SQLite persistence for conversations, messages, and usage.",
        "test_mcp.py": "MCP client/server smoke-test script.",
        "tools.py": "Built-in tool definitions, MCP discovery, and dispatch.",
        "web_search.py": "Web search and webpage retrieval with URL validation.",
        "docs/ARCHITECTURE.md": "Implemented system components and architectural boundaries.",
        "docs/CLEANUP_AUDIT.md": "Cleanup findings, risks, and investigation candidates.",
        "docs/DATA_FLOW.md": "Main chat, approval, authentication, and tool execution flows.",
        "docs/DEPENDENCY_MAP.md": "Generated source imports and declared dependencies.",
        "docs/DEVELOPMENT_LOG.md": "Development progress and validation record.",
        "docs/FEATURE_GUIDE.md": "Feature behavior, implementation locations, and tests.",
        "docs/FUNCTION_INDEX.md": "Generated function, class, method, and constant index.",
        "docs/README.md": "Documentation index and maintenance instructions.",
        "docs/REPOSITORY_MAP.md": "Generated file inventory with purpose and Git state.",
    }
    if name in purposes:
        return purposes[name]
    if name.startswith("src-tauri/icons/"):
        return "Tauri desktop application icon asset."
    if name.startswith("static/icon-"):
        return "Web app icon asset."
    raise ValueError(f"Missing file purpose: {name}")


def inventory():
    tracked = set(git_output("ls-files"))
    status = git_output("status", "--short", "--untracked-files=all")
    untracked = {line[3:] for line in status if line.startswith("?? ")}

    entries = []
    for name in sorted(tracked | untracked):
        if not safe_path(name):
            continue
        path = ROOT / name
        if not path.is_file():
            continue

        category = (
            "Python" if path.suffix == ".py" else
            "JavaScript" if path.suffix == ".js" else
            "CSS" if path.suffix == ".css" else
            "HTML" if path.suffix == ".html" else
            "Rust" if path.suffix == ".rs" else
            "Configuration" if path.name in {
                "package.json", "Cargo.toml", "tauri.conf.json",
                "requirements.txt", "backend.spec",
            } or path.suffix in {".toml", ".json", ".yaml", ".yml"} else
            "Documentation" if path.suffix == ".md" else
            "Other"
        )
        entries.append((
            name, category, file_purpose(name),
            "untracked" if name in untracked else "tracked"
        ))
    return entries


def python_symbols(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []

    result = []

    def walk(nodes, prefix="", include_constants=True):
        for node in nodes:
            if include_constants and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        result.append((node.lineno, "constant", prefix + target.id))

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else (
                    "async function" if isinstance(node, ast.AsyncFunctionDef) else "function"
                )
                result.append((node.lineno, kind, prefix + node.name))
                if isinstance(node, ast.ClassDef):
                    walk(node.body, prefix + node.name + ".", True)
                else:
                    walk(node.body, prefix + node.name + ".", False)

    walk(tree.body)
    return sorted(result, key=lambda item: (item[0], item[1], item[2]))



def js_symbols(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    result = []
    function_patterns = [
        re.compile(r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\("),
        re.compile(
            r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*"
            r"(?:async\s*)?(?:function\b|\([^)]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)"
        ),
    ]
    constant_pattern = re.compile(
        r"^\s*(?:const|let|var)\s+([A-Z][A-Z0-9_]+)\s*="
    )

    for number, line in enumerate(lines, 1):
        function_match = next(
            (pattern.search(line) for pattern in function_patterns
             if pattern.search(line)),
            None
        )
        if function_match:
            result.append((number, "function", function_match.group(1)))
            continue

        constant_match = constant_pattern.search(line)
        if constant_match:
            result.append((number, "constant", constant_match.group(1)))

    return result


def rust_symbols(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []

    result = []
    constant_pattern = re.compile(
        r"^\s*(?:pub\s+)?(?:const|static)\s+(\w+)"
    )
    pattern = re.compile(
        r"^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)|"
        r"^\s*(?:pub\s+)?(struct|enum)\s+(\w+)|"
        r"^\s*impl(?:<[^>]+>)?\s+([\w:<>]+)"
    )

    for number, line in enumerate(lines, 1):
        constant_match = constant_pattern.search(line)
        if constant_match:
            result.append((number, "constant", constant_match.group(1)))

        match = pattern.search(line)
        if match:
            if match.group(1):
                result.append((number, "function", match.group(1)))
            elif match.group(2):
                result.append((number, match.group(2), match.group(3)))
            else:
                result.append((number, "impl", match.group(4)))

    return result


def imports(path):
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split(".")[0])
    return found

def javascript_imports(path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()

    patterns = [
        r"\bfrom\s*[\"']([^\"']+)[\"']",
        r"\bimport\s*[\"']([^\"']+)[\"']",
        r"\brequire\(\s*[\"']([^\"']+)[\"']\s*\)",
    ]
    found = set()
    for pattern in patterns:
        found.update(re.findall(pattern, text))
    return found

def rust_imports(path):
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return set()

    return set(re.findall(r"(?m)^\s*(?:pub\s+)?use\s+([A-Za-z_]\w*)", text))


def frontend_external_assets():
    path = ROOT / "static" / "index.html"
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    return set(re.findall(r"https?://[^\s\"'<>]+", text))

def declared_dependencies():
    result = {"Python": [], "JavaScript": [], "Rust": []}

    requirements = ROOT / "requirements.txt"
    if requirements.exists():
        for line in requirements.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                result["Python"].append(line)

    package = ROOT / "package.json"
    if package.exists():
        data = json.loads(package.read_text(encoding="utf-8"))
        for section in ("dependencies", "devDependencies"):
            for name, version in data.get(section, {}).items():
                result["JavaScript"].append(f"{name} {version} ({section})")

    cargo = ROOT / "src-tauri" / "Cargo.toml"
    if cargo.exists():
        data = tomllib.loads(cargo.read_text(encoding="utf-8"))
        for section in ("dependencies", "build-dependencies"):
            for name, value in data.get(section, {}).items():
                version = value.get("version", "unspecified") if isinstance(value, dict) else value
                result["Rust"].append(f"{name} {version} ({section})")

    return result


def main():
    DOCS.mkdir(exist_ok=True)
    files = inventory()

    inventory_lines = [
        "# Repository Map",
        "",
        "> Generated by `.venv/bin/python scripts/generate_code_map.py`. Do not edit this file manually.",
        "",
        f"Inventory entries: **{len(files)}** (tracked and visible untracked files).",
        "",
        "| Path | Category | Purpose | Git state |",
        "|---|---|---|---|",
    ]
    inventory_lines += [f"| `{name}` | {category} | {purpose} | {state} |" for name, category, purpose, state in files]
    inventory_lines += [
        "",
        "## Exclusions",
        "",
        "This inventory excludes secrets, local databases, pending-action data, Finder metadata, "
        "Python bytecode, virtual environments, dependency directories, caches, and generated "
        "Tauri output. It lists paths only and never reads secret or user-data contents.",
        "",
        "Ignored local directories such as `.venv/`, `node_modules/`, and `src-tauri/gen/` "
        "are intentionally not expanded.",
        "",
    ]
    (DOCS / "REPOSITORY_MAP.md").write_text("\n".join(inventory_lines), encoding="utf-8")

    symbol_lines = [
        "# Function and Symbol Index",
        "",
        "> Generated by `.venv/bin/python scripts/generate_code_map.py`. Line numbers refer to the current working tree.",
        "",
        "Python symbols are extracted with the standard-library AST. JavaScript and Rust "
        "symbols and constants use declaration-pattern matching; these sections are best-effort and may "
        "not include every nested, overloaded, or dynamically declared symbol.",
        "",
    ]
    for language, suffixes, extractor in [
        ("Python", {".py"}, python_symbols),
        ("JavaScript", {".js"}, js_symbols),
        ("Rust", {".rs"}, rust_symbols),
    ]:
        symbol_lines += [f"## {language}", ""]
        for name, category, purpose, state in files:
            path = ROOT / name
            if path.suffix not in suffixes:
                continue
            symbols = extractor(path)
            if symbols:
                symbol_lines += [f"### `{name}`", "", "| Line | Kind | Symbol |", "|---:|---|---|"]
                symbol_lines += [f"| {line} | {kind} | `{symbol}` |" for line, kind, symbol in symbols]
                symbol_lines.append("")
    (DOCS / "FUNCTION_INDEX.md").write_text("\n".join(symbol_lines), encoding="utf-8")

    local_modules = {Path(name).stem for name, _, _, _ in files if name.endswith(".py")}
    dependency_lines = [
        "# Dependency Map",
        "",
        "> Generated from source imports and declared dependency manifests. Static inventory only; "
        "it does not represent runtime call relationships.",
        "",
        "## Local Python module imports",
        "",
    ]
    for name, _, _, _ in files:
        if not name.endswith(".py"):
            continue
        deps = sorted(imports(ROOT / name) & local_modules - {Path(name).stem})
        dependency_lines += [f"### `{name}`", ""]
        dependency_lines += [f"- `{dep}`" for dep in deps] if deps else ["- No imports of other local Python modules detected."]
        dependency_lines.append("")

    dependency_lines += ["## JavaScript imports", ""]
    js_files = [name for name, _, _, _ in files if name.endswith(".js")]
    for name in js_files:
        deps = sorted(javascript_imports(ROOT / name))
        dependency_lines += [f"### `{name}`", ""]
        dependency_lines += [f"- `{dep}`" for dep in deps] if deps else [
            "- No static ES module imports or CommonJS `require()` calls detected."
        ]
        dependency_lines.append("")

    dependency_lines += ["## Frontend CDN assets", ""]
    cdn_assets = sorted(frontend_external_assets())
    dependency_lines += [f"- `{url}`" for url in cdn_assets] if cdn_assets else [
        "- No external CDN assets detected."
    ]
    dependency_lines.append("")

    dependency_lines += ["## Rust crate imports", ""]
    rust_files = [name for name, _, _, _ in files if name.endswith(".rs")]
    for name in rust_files:
        deps = sorted(rust_imports(ROOT / name))
        dependency_lines += [f"### `{name}`", ""]
        dependency_lines += [f"- `{dep}`" for dep in deps] if deps else [
            "- No top-level `use` declarations detected."
        ]
        dependency_lines.append("")

    dependency_lines += ["## Declared external dependencies", ""]
    for language, entries in declared_dependencies().items():
        dependency_lines += [f"### {language}", ""]
        dependency_lines += [f"- `{entry}`" for entry in entries] if entries else [
            "- No dependencies declared in the inspected manifest."
        ]
        dependency_lines.append("")

    dependency_lines += [
        "## Runtime call relationships",
        "",
        "See [Architecture](ARCHITECTURE.md) and [Data Flow](DATA_FLOW.md). "
        "This generated file deliberately does not infer runtime calls from imports.",
        "",
    ]
    (DOCS / "DEPENDENCY_MAP.md").write_text("\n".join(dependency_lines), encoding="utf-8")

    print(f"Generated docs/REPOSITORY_MAP.md ({len(files)} files)")
    print("Generated docs/FUNCTION_INDEX.md")
    print("Generated docs/DEPENDENCY_MAP.md")

if __name__ == "__main__":
    main()
