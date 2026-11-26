import json
import sqlite3
import time
import uuid
from contextlib import closing

from runtime import DATA_DIR


PENDING_DB = DATA_DIR / "pending_google_tasks.sqlite3"
EXPIRY_SECONDS = 30 * 60


class UnavailableTaskProposal(ValueError):
    pass


def _connect():
    connection = sqlite3.connect(PENDING_DB, timeout=10)
    connection.execute(
        "CREATE TABLE IF NOT EXISTS proposals "
        "(id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, "
        "expires_at REAL NOT NULL, payload TEXT NOT NULL)"
    )
    return connection


def save_task_proposal(proposal, conversation_id):
    """Save the resolved task details used by both the approval card and executor."""
    pending_id = uuid.uuid4().hex
    payload = json.dumps(proposal, ensure_ascii=False, allow_nan=False)
    now = time.time()
    with closing(_connect()) as connection, connection:
        connection.execute("DELETE FROM proposals WHERE expires_at <= ?", (now,))
        connection.execute(
            "INSERT INTO proposals (id, conversation_id, expires_at, payload) "
            "VALUES (?, ?, ?, ?)",
            (pending_id, str(conversation_id), now + EXPIRY_SECONDS, payload),
        )
    return {"pending_id": pending_id, "proposal": json.loads(payload)}


def consume_task_proposal(pending_id):
    """Claim the proposal once across simultaneous requests and server processes."""
    with closing(_connect()) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT payload FROM proposals WHERE id = ? AND expires_at > ?",
            (pending_id, time.time()),
        ).fetchone()
        if row is None:
            raise UnavailableTaskProposal(
                "This task proposal expired or has already been handled."
            )
        connection.execute("DELETE FROM proposals WHERE id = ?", (pending_id,))
    return json.loads(row[0])


def prepare_google_task_proposal(arguments, conversation_id):
    """Resolve display details from Google before saving a proposed change."""
    from google_integrations import get_tasks_service, validate_google_task_change

    if not isinstance(arguments, dict):
        raise ValueError("Google Task details must be an object.")
    if arguments.keys() - {"action", "task_list_id", "task_id", "changes"}:
        raise ValueError("Unsupported Google Task proposal field.")

    action = arguments.get("action")
    list_id = arguments.get("task_list_id")
    task_id = arguments.get("task_id")
    changes = arguments.get("changes")
    validate_google_task_change(action, list_id, task_id, changes)

    service = get_tasks_service()
    task_list = service.tasklists().get(tasklist=list_id).execute()
    if task_list.get("id") != list_id or not task_list.get("title"):
        raise ValueError("Could not identify the selected Google task list.")

    proposal = {
        "action": action,
        "task_list_id": list_id,
        "task_list_title": task_list["title"],
        "task_id": task_id,
        "changes": changes or {},
    }
    if action == "create":
        proposal["task_title"] = changes["title"]
    else:
        task = service.tasks().get(tasklist=list_id, task=task_id).execute()
        if task.get("id") != task_id or task.get("deleted"):
            raise ValueError("The selected task is no longer available in this list.")
        proposal["task_title"] = task.get("title") or "(Untitled task)"
        proposal["current"] = {
            "title": task.get("title", ""),
            "notes": task.get("notes", ""),
            "due": (task.get("due") or "")[:10] or None,
            "status": task.get("status"),
        }
    return save_task_proposal(proposal, conversation_id)


def only_list_task_title(message):
    import re

    match = re.fullmatch(
        r'\s*(?:create|add)\s+(?:a\s+)?(?:google\s+)?task\s+'
        r'(?:called|named)\s+(?:"([^"\n]+)"|“([^”\n]+)”)'
        r'\s+in\s+my\s+only\s+task\s+list'
        r'(?:[.,]?\s+(?:leave\s+the\s+due\s+date\s+blank|with\s+no\s+due\s+date))?'
        r'[.!]?\s*',
        message, re.IGNORECASE,
    )
    return (match.group(1) or match.group(2)) if match else None


def prepare_only_list_create(message, conversation_id):
    from google_integrations import get_tasks_service, list_google_task_lists

    title = only_list_task_title(message)
    if title is None:
        return None

    lists = list_google_task_lists(get_tasks_service())
    if len(lists) != 1:
        raise ValueError(
            f"Found {len(lists)} Google task lists. Please specify the list to use."
        )
    return prepare_google_task_proposal({
        "action": "create",
        "task_list_id": lists[0]["id"],
        "changes": {"title": title},
    }, conversation_id)
