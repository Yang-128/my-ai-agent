from mcp.server.fastmcp import FastMCP
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import shutil
import platform
import os
import re
from storage import list_conversations, get_conversation
from web_search import search_web, fetch_webpage
from google_integrations import (
    list_calendar_events,
    get_calendar_event,
    list_google_tasks,
)

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
def search_google_tasks(query: str = "", show_completed: bool = True, max_results: int = 100) -> str:
    """Search Google Tasks separately from Google Calendar events. Returns task title, notes, due date, completion status, and task-list name. Use this to identify and inspect tasks; these are not calendar events."""
    import json
    tasks = list_google_tasks(query, show_completed, max_results)
    return json.dumps(tasks, ensure_ascii=False)


@mcp.tool()
def create_calendar_events(events: list[dict]) -> str:
    """Propose multiple Google Calendar changes in one batch for user approval. Each item must include action (create, update, or delete) and relevant event fields. For timed create/update events use start_time and end_time as ISO 8601 date-times with timezone. For all-day events use start_date and end_date as YYYY-MM-DD dates; end_date is exclusive, so a one-day event on March 15 uses start_date="2027-03-15" and end_date="2027-03-16". For recurring events include recurrence as an array of Google Calendar RRULE strings, e.g. ["RRULE:FREQ=WEEKLY;BYDAY=MO,FR,SA"] or ["RRULE:FREQ=WEEKLY;BYDAY=TU"]. Do not invent times or recurrence end dates; omit UNTIL and COUNT unless specified by the user. A recurring event still needs a valid start and end (date-time or all-day date) defining its occurrence. The user must approve the entire batch before any changes are executed."""
    import json
    if not events:
        raise ValueError("At least one calendar event is required.")
    if len(events) > 50:
        raise ValueError("A maximum of 50 calendar changes can be proposed at once.")
    return json.dumps({
        "requires_user_approval": True,
        "batch": True,
        "events": events,
    }, ensure_ascii=False)


@mcp.tool()
def search_calendar_events(
    time_min: str = "",
    time_max: str = "",
    query: str = "",
    max_results: int = 50,
) -> str:
    """Search Google Calendar events by date range and/or title keywords. For a full local day, use local midnight through the following local midnight as ISO 8601 timestamps with timezone offsets. All-day events are returned with start.date and end.date; timed events use start.dateTime and end.dateTime. Search by date range without a title query first when looking for events on a particular day."""
    import json
    events = list_calendar_events(
        time_min=time_min,
        time_max=time_max,
        query=query,
        max_results=max_results,
    )
    results = []
    for event in events:
        results.append({
            "id": event.get("id"),
            "summary": event.get("summary", "(No title)"),
            "start": event.get("start", {}),
            "end": event.get("end", {}),
            "location": event.get("location", ""),
            "description": event.get("description", ""),
            "htmlLink": event.get("htmlLink", ""),
            "status": event.get("status", ""),
        })
    return json.dumps(results, ensure_ascii=False)


@mcp.tool()
def get_calendar_event_details(event_id: str) -> str:
    """Retrieve the full details of a Google Calendar event using its event ID."""
    import json
    return json.dumps(get_calendar_event(event_id), ensure_ascii=False)


@mcp.tool()
def update_calendar_event(
    event_id: str,
    summary: str = "",
    start_time: str = "",
    end_time: str = "",
    location: str = "",
    description: str = "",
) -> str:
    """Propose changes to an existing Google Calendar event. The user must approve the changes in chat before they are applied. Only non-empty fields are changed."""
    import json
    return json.dumps({
        "requires_user_approval": True,
        "action": "update",
        "event_id": event_id,
        "summary": summary,
        "start_time": start_time,
        "end_time": end_time,
        "location": location,
        "description": description,
    }, ensure_ascii=False)


@mcp.tool()
def delete_calendar_event(event_id: str, summary: str = "") -> str:
    """Propose deleting a Google Calendar event. The user must approve the deletion in chat before it is deleted."""
    import json
    return json.dumps({
        "requires_user_approval": True,
        "action": "delete",
        "event_id": event_id,
        "summary": summary,
    }, ensure_ascii=False)


@mcp.tool()
def send_gmail_message(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    bcc: str = "",
    reply_to: str = "",
    attachments: list[str] | None = None,
) -> str:
    """Prepare an email for user review and explicit approval. Attach files created by mac_create_document by passing their paths relative to ~/Documents/My AI Agent (for example, ["Breakfast report.xlsx"]). The user can edit recipients, subject, body, and attachments before approving. Never claim the email has been sent before approval."""
    import json
    return json.dumps({
        "requires_user_approval": True,
        "to": to,
        "cc": cc,
        "bcc": bcc,
        "reply_to": reply_to,
        "subject": subject,
        "body": body,
        "attachments": attachments or [],
    }, ensure_ascii=False)


@mcp.tool()
def get_current_time() -> str:
    """Get the current local date, weekday, and time. Use the weekday as a cross-check."""
    now = datetime.now(ZoneInfo("America/Vancouver"))
    return f"{now.strftime('%A, %Y-%m-%d %H:%M:%S %Z')} ({now.isoformat()})"


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
    """Search previous conversations using relevance-ranked title, phrase, and keyword matching."""
    query = query.strip()
    if not query:
        raise ValueError("Search query cannot be empty.")
    if len(query) > 500:
        raise ValueError("Search query cannot exceed 500 characters.")

    query_lower = query.casefold()
    terms = list(dict.fromkeys(re.findall(r"\w+", query_lower)))
    if not terms:
        raise ValueError("Search query contains no searchable terms.")

    limit = max(1, min(limit, 10))
    matches = []

    for conversation in list_conversations():
        data = get_conversation(conversation["id"])
        if not data:
            continue

        title = data["title"]
        title_lower = title.casefold()
        title_terms = set(re.findall(r"\w+", title_lower))
        title_overlap = sum(term in title_terms for term in terms)

        score = title_overlap * 4
        if query_lower in title_lower:
            score += 12

        excerpts = []

        for message in data["messages"]:
            content = message["content"] or ""
            lowered = content.casefold()
            content_terms = set(re.findall(r"\w+", lowered))
            overlap = sum(term in content_terms for term in terms)

            message_score = overlap * 2
            if query_lower in lowered:
                message_score += 8

            if message_score:
                score += message_score
                best_term = max(
                    terms,
                    key=lambda term: lowered.count(term),
                )
                index = lowered.find(best_term)
                left = max(0, index - 250)
                right = min(len(content), index + len(best_term) + 500)
                excerpt = content[left:right].strip()

                if left:
                    excerpt = "..." + excerpt
                if right < len(content):
                    excerpt += "..."

                excerpts.append((
                    message_score,
                    f'{message["role"]}: {excerpt}'
                ))

        if score:
            excerpts.sort(key=lambda item: item[0], reverse=True)
            matches.append({
                "id": data["id"],
                "title": title,
                "updated_at": data["updated_at"],
                "score": score,
                "excerpts": [item[1] for item in excerpts[:3]],
            })

    matches.sort(
        key=lambda item: (item["score"], item["updated_at"]),
        reverse=True,
    )
    matches = matches[:limit]

    if not matches:
        return "No matching previous conversations found."

    return "\n\n".join(
        f'Title: {item["title"]}\n'
        f'Conversation ID: {item["id"]}\n'
        f'Updated: {item["updated_at"]}\n'
        f'Relevance score: {item["score"]}\n'
        f'Excerpts:\n' + (
            "\n".join(item["excerpts"])
            if item["excerpts"] else "Title matched."
        )
        for item in matches
    )


@mcp.tool()
def get_conversation_history(
    conversation_id: str,
    limit: int = 20,
    query: str = "",
) -> str:
    """Retrieve relevant messages from a previous conversation, with surrounding context."""
    data = get_conversation(conversation_id)
    if not data:
        return "Conversation not found."

    messages = data["messages"]
    limit = max(1, min(limit, 30))
    query_terms = list(dict.fromkeys(re.findall(r"\w+", query.casefold())))

    if query_terms:
        ranked = []
        for index, message in enumerate(messages):
            content = str(message.get("content") or "")
            lowered = content.casefold()
            overlap = sum(term in set(re.findall(r"\w+", lowered)) for term in query_terms)
            if overlap:
                score = overlap * 2
                if query.casefold() in lowered:
                    score += 8
                ranked.append((score, index))

        selected = set()
        for _, index in sorted(ranked, reverse=True)[:8]:
            selected.update(range(max(0, index - 1), min(len(messages), index + 2)))

        indices = sorted(selected)
        if not indices:
            indices = list(range(max(0, len(messages) - min(limit, 10)), len(messages)))
    else:
        indices = list(range(max(0, len(messages) - limit), len(messages)))

    output = []
    total_chars = 0
    max_chars = 7000

    for index in indices:
        message = messages[index]
        content = str(message.get("content") or "")
        remaining = max_chars - total_chars
        if remaining <= 0:
            break
        if len(content) > 1800:
            content = content[:1800] + "\n[Message truncated]"
        content = content[:remaining]
        output.append(f'{message["role"].upper()}: {content}')
        total_chars += len(content)

    if not output:
        return "This conversation has no messages."

    return (
        f'Title: {data["title"]}\n'
        f'Conversation ID: {data["id"]}\n'
        f'Retrieval: {"query-relevant messages" if query_terms else "most recent messages"}\n\n'
        + "\n\n".join(output)
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


@mcp.tool()
def mac_create_document(
    filename: str,
    content: str,
    format: str = "docx",
    folder: str = "Documents/My AI Agent",
    overwrite: bool = False,
) -> str:
    """Create a document and save it directly to the Mac. Supported formats: docx, pdf, xlsx, pptx, txt, md, csv, html. For docx/pdf/txt/md/html, content is text with optional Markdown-style headings and bullets. For csv, content is CSV text. For xlsx, content must be JSON: {"sheets":[{"name":"Sheet1","rows":[["Column A","Column B"],["Value 1","Value 2"]]}]}. For pptx, content must be JSON: {"slides":[{"title":"Slide title","body":"Slide text"}]}. Default save folder is ~/Documents/My AI Agent. Returns the saved file's full path."""
    import json
    import re

    from pathlib import Path

    fmt = format.strip().lower().lstrip(".")
    supported = {"docx", "pdf", "xlsx", "pptx", "txt", "md", "csv", "html"}
    if fmt not in supported:
        raise ValueError(f"Unsupported format: {fmt}. Supported formats: {', '.join(sorted(supported))}.")

    filename = Path(filename.strip()).name
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", filename).strip(" .")
    if not filename:
        raise ValueError("A valid filename is required.")
    if not filename.lower().endswith(f".{fmt}"):
        filename = f"{filename}.{fmt}"

    destination_folder = (MAC_HOME / folder).resolve()
    if not destination_folder.is_relative_to(MAC_HOME):
        raise ValueError("The save folder must be inside the Mac home folder.")

    destination_folder.mkdir(parents=True, exist_ok=True)
    if destination_folder.is_symlink():
        raise ValueError("Saving through a symbolic-link folder is not allowed.")

    target = (destination_folder / filename).resolve()
    if not target.is_relative_to(MAC_HOME) or target.parent != destination_folder:
        raise ValueError("The output file must be directly inside the selected folder.")
    if target.exists() and not overwrite:
        raise FileExistsError(
            f"{target} already exists. Choose another filename or explicitly set overwrite=true."
        )

    if fmt in {"txt", "md", "html", "csv"}:
        target.write_text(content, encoding="utf-8")

    elif fmt == "docx":
        from docx import Document
        from docx.shared import Inches, Pt

        document = Document()
        section = document.sections[0]
        section.top_margin = Inches(0.75)
        section.bottom_margin = Inches(0.75)
        style = document.styles["Normal"]
        style.font.name = "Aptos"
        style.font.size = Pt(10.5)

        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                document.add_paragraph()
            elif stripped.startswith("# "):
                document.add_heading(stripped[2:], level=1)
            elif stripped.startswith("## "):
                document.add_heading(stripped[3:], level=2)
            elif stripped.startswith("### "):
                document.add_heading(stripped[4:], level=3)
            elif stripped.startswith(("- ", "* ")):
                document.add_paragraph(stripped[2:], style="List Bullet")
            elif re.match(r"^\d+[.)]\s+", stripped):
                document.add_paragraph(re.sub(r"^\d+[.)]\s+", "", stripped), style="List Number")
            else:
                document.add_paragraph(stripped)

        document.save(str(target))

    elif fmt == "pdf":
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_CENTER
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        from xml.sax.saxutils import escape

        styles = getSampleStyleSheet()
        styles.add(ParagraphStyle(
            name="DocumentTitle", parent=styles["Title"],
            alignment=TA_CENTER, spaceAfter=18
        ))
        styles.add(ParagraphStyle(
            name="DocumentHeading", parent=styles["Heading1"],
            textColor=colors.HexColor("#243b53"), spaceBefore=12, spaceAfter=6
        ))
        story = []
        for line in content.splitlines():
            stripped = line.strip()
            if not stripped:
                story.append(Spacer(1, 6))
            elif stripped.startswith("# "):
                story.append(Paragraph(escape(stripped[2:]), styles["DocumentTitle"]))
            elif stripped.startswith("## "):
                story.append(Paragraph(escape(stripped[3:]), styles["DocumentHeading"]))
            elif stripped.startswith("### "):
                story.append(Paragraph(escape(stripped[4:]), styles["Heading2"]))
            elif stripped.startswith(("- ", "* ")):
                story.append(Paragraph("&#8226; " + escape(stripped[2:]), styles["BodyText"]))
            else:
                story.append(Paragraph(escape(stripped), styles["BodyText"]))
                story.append(Spacer(1, 4))

        pdf = SimpleDocTemplate(
            str(target), pagesize=LETTER,
            rightMargin=0.8 * inch, leftMargin=0.8 * inch,
            topMargin=0.75 * inch, bottomMargin=0.75 * inch
        )
        pdf.build(story)

    elif fmt == "xlsx":
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from openpyxl.utils import get_column_letter

        data = json.loads(content)
        sheets = data.get("sheets")
        if not isinstance(sheets, list) or not sheets:
            raise ValueError('XLSX content must contain a non-empty "sheets" array.')

        workbook = Workbook()
        workbook.remove(workbook.active)
        for sheet_data in sheets:
            title = str(sheet_data.get("name", "Sheet"))[:31] or "Sheet"
            sheet = workbook.create_sheet(title=title)
            rows = sheet_data.get("rows", [])
            for row in rows:
                sheet.append(row)
            if sheet.max_row:
                for cell in sheet[1]:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="243B53")
                    cell.alignment = Alignment(wrap_text=True)
                sheet.freeze_panes = "A2"
                sheet.auto_filter.ref = sheet.dimensions
                for column in sheet.columns:
                    letter = get_column_letter(column[0].column)
                    width = min(max(max(len(str(c.value or "")) for c in column) + 2, 12), 42)
                    sheet.column_dimensions[letter].width = width
        workbook.save(str(target))

    elif fmt == "pptx":
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.dml.color import RGBColor

        data = json.loads(content)
        slides = data.get("slides")
        if not isinstance(slides, list) or not slides:
            raise ValueError('PPTX content must contain a non-empty "slides" array.')

        presentation = Presentation()
        presentation.slide_width = Inches(13.333)
        presentation.slide_height = Inches(7.5)
        for index, item in enumerate(slides):
            layout = presentation.slide_layouts[0] if index == 0 else presentation.slide_layouts[1]
            slide = presentation.slides.add_slide(layout)
            slide.shapes.title.text = str(item.get("title", f"Slide {index + 1}"))
            title_run = slide.shapes.title.text_frame.paragraphs[0].runs[0]
            title_run.font.size = Pt(30)
            title_run.font.bold = True
            title_run.font.color.rgb = RGBColor(36, 59, 83)

            body = str(item.get("body", ""))
            if len(slide.placeholders) > 1:
                frame = slide.placeholders[1].text_frame
                frame.clear()
                for line_index, line in enumerate(body.splitlines()):
                    paragraph = frame.paragraphs[0] if line_index == 0 else frame.add_paragraph()
                    paragraph.text = line.lstrip("-• ").strip()
                    paragraph.font.size = Pt(20)
                    paragraph.space_after = Pt(10)
                    paragraph.level = 0
        presentation.save(str(target))

    return f"Document created successfully: {target}"


@mcp.tool()
def web_search(query: str, max_results: int = 5) -> str:
    """Search the public web. Returns result titles, URLs, and snippets. Supports site: filters. Use web_fetch to read relevant result pages and documents."""
    return search_web(query, max_results)


@mcp.tool()
def web_fetch(url: str, max_chars: int = 18000) -> str:
    """Open a public HTTP/HTTPS webpage or PDF and extract its readable text. Returns the final URL, page title or PDF page numbers, extracted text, and relevant links. Use this after web_search or when the user supplies a URL."""
    return fetch_webpage(url, max_chars)


if __name__ == "__main__":
    mcp.run(transport="stdio")
