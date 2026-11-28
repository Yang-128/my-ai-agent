"""Shared source-selection policy for the first coding workspace."""
import re
from pathlib import PurePosixPath

MAX_FILE_BYTES = 384_000
MAX_SOURCE_BYTES = 6_000_000
MAX_FILES = 400
MAX_PATH = 240
ROOT_FILES = {"requirements.txt", "package.json", "README.md", "AGENTS.md",
              "PROJECT_CONTEXT.md", "backend.spec"}
DIRECTORIES = {
    "docs": {".md"},
    "scripts": {".py", ".sh"},
    "tests": {".py", ".json", ".txt"},
    "static": {".js", ".css", ".html", ".json", ".webmanifest", ".svg"},
    "coding_sandbox": {".py"},
}


def allowed_source(path):
    """Only known source/document types; never hidden or runtime files."""
    if not isinstance(path, str) or not 0 < len(path) <= MAX_PATH:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_./-]+", path):
        return False
    parts = path.split("/")
    if any(not part or part.startswith(".") or part == "__pycache__" for part in parts):
        return False
    if any(part in {"node_modules", "dist", "target", "data", "secrets"} for part in parts):
        return False
    suffix = PurePosixPath(path).suffix
    if len(parts) == 1:
        return path in ROOT_FILES or suffix == ".py"
    if path == "coding_sandbox/Dockerfile":
        return True
    return suffix in DIRECTORIES.get(parts[0], set())
