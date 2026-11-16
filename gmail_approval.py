import json
import threading
import uuid
import mimetypes
from pathlib import Path
from datetime import datetime, timedelta, timezone

from runtime import DATA_DIR

PENDING_FILE = DATA_DIR / "pending_gmail_messages.json"
_LOCK = threading.Lock()
_EXPIRY = timedelta(minutes=30)
DOCUMENTS_ROOT = (Path.home() / "Documents" / "My AI Agent").resolve()
MAX_ATTACHMENT_SIZE = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv", ".pdf", ".docx", ".doc", ".pptx", ".txt", ".md", ".html", ".png", ".jpg", ".jpeg", ".webp"}


def validate_attachments(value):
    if value is None:
        value = []
    if not isinstance(value, list) or len(value) > 20:
        raise ValueError("Attachments must be a list of no more than 20 files.")
    result = []
    total_size = 0
    for raw in value:
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("Each attachment must be a relative file path.")
        candidate = Path(raw.strip())
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("Attachments must be selected from the My AI Agent Documents folder.")
        target = DOCUMENTS_ROOT / candidate
        if target.is_symlink():
            raise ValueError("Symbolic links cannot be attached.")
        resolved = target.resolve()
        if not resolved.is_relative_to(DOCUMENTS_ROOT) or not resolved.is_file():
            raise ValueError(f"Attachment not found: {raw}")
        if any(part.startswith(".") for part in resolved.relative_to(DOCUMENTS_ROOT).parts):
            raise ValueError("Hidden files cannot be attached.")
        if resolved.suffix.lower() not in ALLOWED_EXTENSIONS:
            raise ValueError(f"Unsupported attachment type: {resolved.suffix}")
        size = resolved.stat().st_size
        if size > MAX_ATTACHMENT_SIZE:
            raise ValueError(f"Attachment exceeds 20 MB: {resolved.name}")
        total_size += size
        if total_size > MAX_ATTACHMENT_SIZE:
            raise ValueError("Combined attachments cannot exceed 20 MB.")
        relative = resolved.relative_to(DOCUMENTS_ROOT).as_posix()
        if relative not in result:
            result.append(relative)
    return result


def _read():
    if not PENDING_FILE.exists():
        return {}
    try:
        data = json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary = PENDING_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(PENDING_FILE)
    PENDING_FILE.chmod(0o600)


def create_pending_gmail_message(arguments, conversation_id):
    if not isinstance(arguments, dict):
        raise ValueError("Email details must be an object.")

    message = {
        "to": str(arguments.get("to", "")).strip(),
        "cc": str(arguments.get("cc", "")).strip(),
        "bcc": str(arguments.get("bcc", "")).strip(),
        "reply_to": str(arguments.get("reply_to", "")).strip(),
        "subject": str(arguments.get("subject", "")).strip(),
        "body": str(arguments.get("body", "")),
        "attachments": validate_attachments(arguments.get("attachments", [])),
    }

    if not message["to"]:
        raise ValueError("At least one recipient is required.")
    if not message["subject"]:
        raise ValueError("An email subject is required.")
    if not message["body"].strip():
        raise ValueError("An email body is required.")
    if len(message["to"]) > 2000 or len(message["cc"]) > 2000 or len(message["bcc"]) > 2000:
        raise ValueError("Recipient fields are too long.")
    if len(message["subject"]) > 998 or len(message["body"]) > 50000:
        raise ValueError("The email exceeds the allowed size.")

    pending_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)

    with _LOCK:
        data = _read()
        valid_items = {}
        for key, item in data.items():
            try:
                created = datetime.fromisoformat(item["created_at"])
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                if created > now - _EXPIRY:
                    valid_items[key] = item
            except (KeyError, TypeError, ValueError):
                continue
        data = valid_items
        data[pending_id] = {
            "message": message,
            "conversation_id": str(conversation_id),
            "created_at": now.isoformat(),
        }
        _write(data)

    return {"pending_id": pending_id, "message": message}


def consume_pending_gmail_message(pending_id):
    with _LOCK:
        data = _read()
        item = data.pop(str(pending_id), None)
        _write(data)

    if not item:
        raise ValueError("This email proposal has expired or has already been handled.")

    created = datetime.fromisoformat(item["created_at"])
    if datetime.now(timezone.utc) - created > _EXPIRY:
        raise ValueError("This email proposal has expired. Ask the assistant to prepare it again.")

    return item


def discard_pending_gmail_message(pending_id):
    with _LOCK:
        data = _read()
        existed = data.pop(str(pending_id), None) is not None
        _write(data)
    return existed
