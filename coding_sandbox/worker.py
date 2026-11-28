"""Trusted helper installed outside the writable coding workspace."""
import json
import sys
from pathlib import Path

from policy import allowed_source, MAX_FILE_BYTES, MAX_FILES, MAX_SOURCE_BYTES

ROOT = Path("/workspace").resolve()


def target(path):
    if not allowed_source(path):
        raise ValueError("Path is outside the allowed source set")
    result = ROOT / path
    if any(p.is_symlink() for p in (result, *result.parents) if p != ROOT):
        raise ValueError("Symlinks are not supported")
    if not result.resolve().is_relative_to(ROOT):
        raise ValueError("Path escapes workspace")
    return result


def read(path):
    p = target(path)
    if not p.is_file() or p.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("Not a bounded regular source file")
    return p.read_bytes().decode("utf-8")


def snapshot():
    files = {}
    size = 0
    # Do not follow directory symlinks. Refuse any new symlink in source areas.
    for p in sorted(ROOT.rglob("*")):
        name = p.relative_to(ROOT).as_posix()
        if p.is_symlink():
            raise ValueError("Workspace contains an unsupported symlink")
        if not p.is_file() or not allowed_source(name):
            continue
        content = read(name)
        size += len(content.encode("utf-8"))
        if len(files) >= MAX_FILES or size > MAX_SOURCE_BYTES:
            raise ValueError("Workspace source limit exceeded")
        files[name] = content
    return files


def action(data):
    op = data["operation"]
    if op == "list_files":
        return sorted(snapshot())
    if op == "read_file":
        lines = read(data["path"]).splitlines()
        start = data.get("start_line", 1)
        if type(start) is not int or start < 1:
            raise ValueError("Invalid start line")
        end = min(start + 159, len(lines))
        text = "\n".join(f"{i + 1}: {lines[i]}" for i in range(start - 1, end))
        return {"total_lines": len(lines), "text": text[:16000], "next_line": end + 1 if end < len(lines) else None}
    if op == "search":
        needle = data.get("query", "")
        if not isinstance(needle, str) or not 1 <= len(needle) <= 160:
            raise ValueError("Search requires 1–160 literal characters")
        matches = []
        for path, content in snapshot().items():
            for number, line in enumerate(content.splitlines(), 1):
                if needle.lower() in line.lower():
                    matches.append({"path": path, "line": number, "text": line[:240]})
                    if len(matches) >= 45:
                        return matches
        return matches
    if op in {"replace", "create_file"}:
        path = data["path"]
        p = target(path)
        new = data.get("new")
        if not isinstance(new, str):
            raise ValueError("new must be text")
        if op == "replace":
            old = data.get("old")
            content = read(path)
            if not isinstance(old, str) or not old or content.count(old) != 1:
                raise ValueError("old text must match exactly once")
            new = content.replace(old, new, 1)
        elif p.exists():
            raise ValueError("File already exists; use replace")
        if len(new.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("File too large")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(new.encode("utf-8"))
        return {"changed": path, "bytes": len(new.encode("utf-8"))}
    if op == "snapshot":
        return snapshot()
    raise ValueError("Unknown workspace operation")


def main():
    try:
        result = action(json.load(sys.stdin))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({"error": str(exc)[:300]}, ensure_ascii=True))
        return 2
    print(json.dumps({"result": result}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
