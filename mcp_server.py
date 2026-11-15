from mcp.server.fastmcp import FastMCP
from datetime import datetime
from pathlib import Path
import shutil
import platform
import os
from storage import list_conversations, get_conversation

mcp = FastMCP("My AI Agent")




MAC_HOME = Path.home().resolve()
MAC_TEXT_EXTENSIONS = {
    ".txt", ".md", ".py", ".js", ".ts", ".tsx", ".jsx", ".html",
    ".css", ".json", ".csv", ".xml", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".conf", ".log", ".sh", ".zsh", ".bash",
    ".sql", ".java", ".c", ".h", ".cpp", ".hpp", ".rs", ".go",
    ".swift", ".kt", ".rb", ".php", ".r", ".tex",
}
MAC_MAX_READ_SIZE = 1_000_000


def resolve_mac_path(path="/"):
    target = Path(path).expanduser().resolve()
    if not target.exists():
        raise FileNotFoundError(str(target))
    return target


def mac_walk_directories(start):
    for current, dirs, files in os.walk(start, followlinks=True):
        current_path = Path(current)
        dirs[:] = [
            name for name in dirs
            if not (current_path / name).is_symlink()
        ]
        yield current_path, files



@mcp.tool()
def get_current_time() -> str:
    """Get the current local date and time."""
    return datetime.now().astimezone().isoformat()


@mcp.tool()
def read_text_file(path: str) -> str:
    """Read a text file inside the project directory."""
    root = Path(__file__).resolve().parent
    target = (root / path).resolve()

    if not target.is_relative_to(root):
        raise ValueError("Access outside the project directory is not allowed.")

    if not target.is_file():
        raise FileNotFoundError(path)

    return target.read_text(encoding="utf-8")[:20000]


@mcp.tool()
def search_conversations(query: str, limit: int = 5) -> str:
    """Search previous conversations by title and message content. Returns matching conversations with relevant message excerpts and their IDs."""
    query = query.strip().lower()
    if not query:
        raise ValueError("Search query cannot be empty.")

    limit = max(1, min(limit, 10))
    matches = []

    for conversation in list_conversations():
        data = get_conversation(conversation["id"])
        if not data:
            continue

        excerpts = []
        title_match = query in data["title"].lower()

        for message in data["messages"]:
            content = message["content"]
            if query in content.lower():
                index = content.lower().find(query)
                start = max(0, index - 250)
                end = min(len(content), index + len(query) + 500)
                excerpt = content[start:end]
                if start:
                    excerpt = "..." + excerpt
                if end < len(content):
                    excerpt += "..."
                excerpts.append(f'{message["role"]}: {excerpt}')

        if title_match or excerpts:
            matches.append({
                "id": data["id"],
                "title": data["title"],
                "updated_at": data["updated_at"],
                "excerpts": excerpts[:3],
            })

        if len(matches) >= limit:
            break

    if not matches:
        return "No matching previous conversations found."

    return "\n\n".join(
        f'Title: {item["title"]}\n'
        f'Conversation ID: {item["id"]}\n'
        f'Updated: {item["updated_at"]}\n'
        f'Excerpts:\n' + (
            "\n".join(item["excerpts"])
            if item["excerpts"] else "Title matched."
        )
        for item in matches
    )


@mcp.tool()
def get_conversation_history(conversation_id: str, limit: int = 30) -> str:
    """Retrieve messages from a previous conversation using its conversation ID. Returns the most recent messages."""
    data = get_conversation(conversation_id)
    if not data:
        return "Conversation not found."

    limit = max(1, min(limit, 100))
    messages = data["messages"][-limit:]

    if not messages:
        return "This conversation has no messages."

    return (
        f'Title: {data["title"]}\n'
        f'Conversation ID: {data["id"]}\n\n'
        + "\n\n".join(
            f'{message["role"].upper()}: {message["content"]}'
            for message in messages
        )
    )



@mcp.tool()
def mac_list_files(path: str = ".", limit: int = 200) -> str:
    """List files and folders inside the Mac user's home folder. Hidden and protected locations are excluded."""
    directory = resolve_mac_path(path)
    if not directory.is_dir():
        raise NotADirectoryError(path)

    limit = max(1, min(limit, 500))
    entries = []
    for entry in directory.iterdir():
        try:
            target = resolve_mac_path(str(entry))
            if not target.exists() or entry.is_symlink():
                continue
            is_dir = target.is_dir()
            size = "" if is_dir else f" ({target.stat().st_size:,} bytes)"
            entries.append((not is_dir, entry.name.lower(),
                            f"{'Folder' if is_dir else 'File'}: {entry.name}{size}"))
        except (ValueError, OSError):
            continue

    entries.sort(key=lambda item: (item[0], item[1]))
    output = [item[2] for item in entries[:limit]]
    location = str(directory) or "."
    if len(entries) > limit:
        output.append(f"... and {len(entries) - limit} more items.")
    return f"Contents of {location}:\n" + ("\n".join(output) if output else "This folder is empty.")


@mcp.tool()
def mac_search_files(query: str, path: str = ".", limit: int = 20) -> str:
    """Search filenames and text-file contents recursively inside the Mac user's home folder."""
    query = query.strip().lower()
    if not query:
        raise ValueError("Search query cannot be empty.")

    start = resolve_mac_path(path)
    if not start.is_dir():
        raise NotADirectoryError(path)

    limit = max(1, min(limit, 50))
    matches = []
    scanned = 0

    for folder, filenames in mac_walk_directories(start):
        for filename in filenames:
            if filename.startswith("."):
                continue
            candidate = folder / filename
            try:
                target = resolve_mac_path(str(candidate))
                if not target.is_file() or candidate.is_symlink():
                    continue

                relative = target.as_posix()
                if query in filename.lower():
                    matches.append(f"Filename match: {relative}")
                elif target.suffix.lower() in MAC_TEXT_EXTENSIONS:
                    scanned += 1
                    if target.stat().st_size <= MAC_MAX_READ_SIZE:
                        content = target.read_text(encoding="utf-8", errors="ignore")
                        index = content.lower().find(query)
                        if index >= 0:
                            excerpt = content[max(0, index - 100):index + len(query) + 180]
                            matches.append(f"Content match: {relative}\n  ...{excerpt}...")

                if len(matches) >= limit:
                    return "\n".join(matches)
                if scanned >= 5000:
                    matches.append("Search stopped after scanning 5,000 text files. Narrow the starting folder to continue.")
                    return "\n".join(matches)
            except (ValueError, OSError):
                continue

    return "\n".join(matches) if matches else "No matching files found."


@mcp.tool()
def mac_read_file(path: str) -> str:
    """Read text, PDF, Word, Excel, and PowerPoint documents anywhere accessible on this Mac."""
    target = resolve_mac_path(path)

    if not target.is_file():
        raise FileNotFoundError(path)

    if target.stat().st_size > 20_000_000:
        raise ValueError("File exceeds the 20 MB reading limit.")

    suffix = target.suffix.lower()
    limit = 30000

    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(target))
        parts = []
        for index, page in enumerate(reader.pages[:100]):
            text = page.extract_text() or ""
            if text.strip():
                parts.append(f"Page {index + 1}:\n{text}")
        content = "\n\n".join(parts)

    elif suffix == ".docx":
        from docx import Document
        document = Document(str(target))
        parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        for table_index, table in enumerate(document.tables, 1):
            parts.append(f"Table {table_index}:")
            for row in table.rows:
                parts.append(" | ".join(cell.text for cell in row.cells))
        content = "\n".join(parts)

    elif suffix == ".xlsx":
        from openpyxl import load_workbook
        workbook = load_workbook(str(target), read_only=True, data_only=True)
        parts = []
        try:
            for sheet in workbook.worksheets:
                parts.append(f"Sheet: {sheet.title}")
                for row in sheet.iter_rows(values_only=True):
                    values = ["" if value is None else str(value) for value in row]
                    if any(values):
                        parts.append(" | ".join(values))
        finally:
            workbook.close()
        content = "\n".join(parts)

    elif suffix == ".pptx":
        from pptx import Presentation
        presentation = Presentation(str(target))
        parts = []
        for index, slide in enumerate(presentation.slides, 1):
            parts.append(f"Slide {index}:")
            for shape in slide.shapes:
                if getattr(shape, "has_text_frame", False) and shape.text.strip():
                    parts.append(shape.text)
                if getattr(shape, "has_table", False):
                    for row in shape.table.rows:
                        parts.append(" | ".join(cell.text for cell in row.cells))
        content = "\n".join(parts)

    elif suffix in MAC_TEXT_EXTENSIONS:
        content = target.read_text(encoding="utf-8", errors="replace")
        if "\x00" in content:
            raise ValueError("This appears to be a binary file.")

    else:
        raise ValueError(
            f"Unsupported file type: {suffix or 'no extension'}. "
            "Supported formats include text, PDF, DOCX, XLSX, and PPTX."
        )

    if not content.strip():
        return f"No extractable text found in {target}."

    truncated = len(content) > limit
    content = content[:limit]
    return (
        f"File: {target}\n"
        f"Format: {suffix}\n\n"
        f"{content}"
        + ("\n\n[Content truncated at 30,000 characters.]" if truncated else "")
    )


@mcp.tool()
def mac_search_file_contents(query: str, path: str = ".", limit: int = 20, max_files: int = 2000) -> str:
    """Search inside supported documents and text files on this Mac, returning distinct excerpts with page, sheet, or slide context."""
    query = query.strip()
    if not query:
        raise ValueError("Search query cannot be empty.")

    start = resolve_mac_path(path)
    if not start.is_dir():
        raise NotADirectoryError(path)

    limit = max(1, min(limit, 50))
    max_files = max(1, min(max_files, 5000))
    extensions = MAC_TEXT_EXTENSIONS | {".pdf", ".docx", ".xlsx", ".pptx"}
    matches = []
    scanned = 0
    scan_limit_reached = False
    query_lower = query.lower()

    def extract_sections(target):
        suffix = target.suffix.lower()

        if suffix == ".pdf":
            from pypdf import PdfReader
            reader = PdfReader(str(target))
            for index, page in enumerate(reader.pages[:100], 1):
                text = page.extract_text() or ""
                if text.strip():
                    yield f"Page {index}", text

        elif suffix == ".xlsx":
            from openpyxl import load_workbook
            workbook = load_workbook(str(target), read_only=True, data_only=True)
            try:
                for sheet in workbook.worksheets:
                    parts = []
                    for row in sheet.iter_rows(values_only=True):
                        values = ["" if value is None else str(value) for value in row]
                        if any(values):
                            parts.append(" | ".join(values))
                    if parts:
                        yield f"Sheet: {sheet.title}", "\n".join(parts)
            finally:
                workbook.close()

        elif suffix == ".docx":
            from docx import Document
            document = Document(str(target))
            parts = [x.text for x in document.paragraphs if x.text.strip()]
            for table_index, table in enumerate(document.tables, 1):
                parts.append(f"Table {table_index}:")
                for row in table.rows:
                    parts.append(" | ".join(cell.text for cell in row.cells))
            if parts:
                yield "Document", "\n".join(parts)

        elif suffix == ".pptx":
            from pptx import Presentation
            presentation = Presentation(str(target))
            for index, slide in enumerate(presentation.slides, 1):
                parts = []
                for shape in slide.shapes:
                    if getattr(shape, "has_text_frame", False) and shape.text.strip():
                        parts.append(shape.text)
                    if getattr(shape, "has_table", False):
                        for row in shape.table.rows:
                            parts.append(" | ".join(cell.text for cell in row.cells))
                if parts:
                    yield f"Slide {index}", "\n".join(parts)

        elif suffix in MAC_TEXT_EXTENSIONS:
            content = target.read_text(encoding="utf-8", errors="replace")
            if "\x00" not in content and content.strip():
                yield "Text", content

    for folder, filenames in mac_walk_directories(start):
        for filename in filenames:
            if filename.startswith("."):
                continue

            candidate = folder / filename
            try:
                target = resolve_mac_path(str(candidate))
                if candidate.is_symlink() or not target.is_file():
                    continue
                if target.suffix.lower() not in extensions:
                    continue
                if target.stat().st_size > 20_000_000:
                    continue

                if scanned >= max_files:
                    scan_limit_reached = True
                    break

                scanned += 1
                excerpts = []
                previous_match = None

                for section, content in extract_sections(target):
                    lowered = content.lower()
                    offset = 0

                    while True:
                        index = lowered.find(query_lower, offset)
                        if index < 0:
                            break

                        if previous_match is None or previous_match[0] != section or index - previous_match[1] >= 500:
                            left = max(0, index - 180)
                            right = min(len(content), index + len(query) + 260)
                            excerpt = content[left:right].strip()
                            excerpts.append(f"{section}: ...{excerpt}...")
                            previous_match = (section, index)

                        offset = index + len(query)

                        if len(excerpts) >= 3:
                            break

                    if len(excerpts) >= 3:
                        break

                if excerpts:
                    matches.append(
                        f"Match: {target}\n" + "\n".join(
                            f"Excerpt {i + 1}: {excerpt}"
                            for i, excerpt in enumerate(excerpts)
                        )
                    )

                    if len(matches) >= limit:
                        break

            except Exception:
                continue

        if scan_limit_reached or len(matches) >= limit:
            break

    if matches:
        result = (
            f"Found {len(matches)} matching files (searched {scanned} files):\n\n"
            + "\n\n".join(matches)
        )
    else:
        result = f"No matching file contents found. Searched {scanned} files."

    if scan_limit_reached:
        result += (
            f"\n\nScan limit reached ({max_files} files). "
            "Narrow the starting folder or increase max_files."
        )

    return result


@mcp.tool()
def mac_get_system_info() -> str:
    """Get basic read-only information about this Mac."""
    usage = shutil.disk_usage(MAC_HOME)
    gib = 1024 ** 3
    return (
        f"System: {platform.system()} {platform.release()}\n"
        f"macOS: {platform.mac_ver()[0] or 'Unknown'}\n"
        f"Architecture: {platform.machine()}\n"
        f"Logical CPU cores: {os.cpu_count() or 'Unknown'}\n"
        f"Home-folder disk usage: {usage.used / gib:.1f} GiB used / "
        f"{usage.total / gib:.1f} GiB total ({usage.free / gib:.1f} GiB free)"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
