import json
import os

# Local development only: permit OAuth over localhost HTTP.
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from runtime import DATA_DIR, ENV_FILE

load_dotenv(ENV_FILE, override=True)

REDIRECT_URI = "http://localhost:8000/api/google/callback"
TASKS_WRITE_SCOPE = "https://www.googleapis.com/auth/tasks"
SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    TASKS_WRITE_SCOPE,
    "https://www.googleapis.com/auth/gmail.send",
]

TOKEN_FILE = DATA_DIR / "google_calendar_token.json"
STATE_FILE = DATA_DIR / "google_oauth_state.json"


def _client_config():
    client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        raise RuntimeError("Google OAuth credentials are missing from .env.")

    return {
        "web": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [REDIRECT_URI],
        }
    }


CALENDAR_ACCOUNT_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


def _calendar_signin_path(state):
    import hashlib

    if not isinstance(state, str) or not state or len(state) > 512:
        raise ValueError("Invalid calendar sign-in state.")
    digest = hashlib.sha256(state.encode()).hexdigest()
    return DATA_DIR / f"google_calendar_signin_{digest}.json"


def _write_private_json_file(path, text):
    """Create a private file exclusively, without replacing an existing account."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def start_additional_calendar_oauth():
    import time
    import uuid

    flow = Flow.from_client_config(
        _client_config(), scopes=CALENDAR_ACCOUNT_SCOPES, redirect_uri=REDIRECT_URI
    )
    url, state = flow.authorization_url(
        access_type="offline", prompt="select_account consent"
    )
    _write_private_json_file(_calendar_signin_path(state), json.dumps({
        "account_id": uuid.uuid4().hex,
        "code_verifier": flow.code_verifier,
        "expires_at": time.time() + 900,
    }))
    return url


def finish_additional_calendar_oauth(authorization_response, state):
    import time

    path = _calendar_signin_path(state)
    claimed = path.with_suffix(".processing")
    try:
        path.rename(claimed)
    except FileNotFoundError:
        raise ValueError("Calendar sign-in expired or was already handled. Start again.") from None

    try:
        session = json.loads(claimed.read_text(encoding="utf-8"))
        if time.time() >= session["expires_at"]:
            raise ValueError("Calendar sign-in expired. Start again.")
        account_id = session["account_id"]
        if account_id == "default":
            raise ValueError("Invalid additional calendar account.")
        destination = calendar_token_path(account_id)
        flow = Flow.from_client_config(
            _client_config(), scopes=CALENDAR_ACCOUNT_SCOPES,
            redirect_uri=REDIRECT_URI, state=state,
        )
        flow.code_verifier = session["code_verifier"]
        flow.fetch_token(authorization_response=authorization_response)
        credentials = flow.credentials
        if not credentials.refresh_token:
            raise ValueError("Google did not grant offline access. Start calendar sign-in again.")
        granted = credentials.granted_scopes
        if granted is not None and not set(CALENDAR_ACCOUNT_SCOPES).issubset(granted):
            raise ValueError("Google Calendar permission was not granted.")
        _write_private_json_file(destination, credentials.to_json())
        return account_id
    finally:
        claimed.unlink(missing_ok=True)


def start_google_oauth():
    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI,
    )
    url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    STATE_FILE.write_text(json.dumps({
        "state": state,
        "code_verifier": flow.code_verifier,
    }))
    os.chmod(STATE_FILE, 0o600)
    return url


def finish_google_oauth(authorization_response, state):
    if not STATE_FILE.exists():
        raise RuntimeError("OAuth session expired. Please connect again.")

    oauth_state = json.loads(STATE_FILE.read_text())
    expected = oauth_state.get("state")
    if not state or state != expected:
        raise RuntimeError("OAuth state validation failed.")

    flow = Flow.from_client_config(
        _client_config(),
        scopes=SCOPES,
        state=expected,
        redirect_uri=REDIRECT_URI,
    )
    flow.code_verifier = oauth_state.get("code_verifier")
    flow.fetch_token(authorization_response=authorization_response)
    credentials = flow.credentials
    if credentials.granted_scopes is not None:
        if TASKS_WRITE_SCOPE not in credentials.granted_scopes:
            raise RuntimeError("Reconnect Google and grant permission to manage Google Tasks.")

    # Google may omit a new refresh token when the account has authorized
    # this application before. Preserve the existing token for the same user.
    if not credentials.refresh_token and TOKEN_FILE.exists():
        try:
            existing = Credentials.from_authorized_user_file(
                str(TOKEN_FILE),
                scopes=SCOPES,
            )
            credentials.refresh_token = existing.refresh_token
        except Exception:
            pass

    if not credentials.refresh_token:
        raise RuntimeError(
            "Google did not provide a refresh token. Revoke this app's access "
            "in your Google Account and connect again."
        )

    TOKEN_FILE.write_text(credentials.to_json())
    os.chmod(TOKEN_FILE, 0o600)
    STATE_FILE.unlink(missing_ok=True)


def calendar_token_path(account_id="default"):
    """Keep the original connection in place and isolate additional accounts."""
    from uuid import UUID

    if account_id == "default":
        return TOKEN_FILE
    try:
        key = UUID(hex=account_id)
    except (ValueError, TypeError, AttributeError):
        raise ValueError("Invalid calendar account ID.") from None
    if key.hex != account_id:
        raise ValueError("Invalid calendar account ID.")
    return DATA_DIR / f"google_calendar_token_{key.hex}.json"


def get_google_credentials(*, require_tasks_write=False, account_id="default"):
    token_file = calendar_token_path(account_id)
    if not token_file.exists():
        raise RuntimeError("Google Calendar is not connected.")

    credentials = Credentials.from_authorized_user_file(str(token_file))
    if require_tasks_write and not credentials.has_scopes([TASKS_WRITE_SCOPE]):
        raise RuntimeError("Reconnect Google and grant permission to manage Google Tasks.")

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        token_file.write_text(credentials.to_json())
        os.chmod(token_file, 0o600)

    if not credentials.valid:
        raise RuntimeError("Google authorization expired. Reconnect Google Calendar.")

    return credentials


def get_gmail_service():
    return build(
        "gmail",
        "v1",
        credentials=get_google_credentials(),
        cache_discovery=False,
    )


def google_gmail_status():
    if not TOKEN_FILE.exists():
        return {"connected": False}

    try:
        credentials = get_google_credentials()
        granted_scopes = set(credentials.scopes or [])
        gmail_scope = "https://www.googleapis.com/auth/gmail.send"

        if gmail_scope not in granted_scopes:
            return {
                "connected": False,
                "error": "Gmail permission is missing. Reconnect your Google account.",
            }

        return {"connected": True, "email": ""}
    except Exception as error:
        return {"connected": False, "error": str(error)}


def send_gmail_message(to, subject, body, cc="", bcc="", reply_to="", attachments=None):
    from email.message import EmailMessage
    import base64

    to = str(to or "").strip()
    subject = str(subject or "").strip()
    body = str(body or "")
    if not to:
        raise ValueError("At least one recipient is required.")
    if not subject:
        raise ValueError("An email subject is required.")
    if not body.strip():
        raise ValueError("An email body is required.")
    if len(to) > 2000 or len(subject) > 998 or len(body) > 50000:
        raise ValueError("The email exceeds the allowed size.")

    message = EmailMessage()
    message["To"] = to
    message["Subject"] = subject
    if cc:
        message["Cc"] = str(cc).strip()
    if bcc:
        message["Bcc"] = str(bcc).strip()
    if reply_to:
        message["Reply-To"] = str(reply_to).strip()
    message.set_content(body)

    from pathlib import Path
    documents_root = (Path.home() / "Documents" / "My AI Agent").resolve()
    total_size = 0
    for relative_path in attachments or []:
        candidate = Path(str(relative_path))
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError("Invalid attachment path.")
        target = documents_root / candidate
        if target.is_symlink():
            raise ValueError("Symbolic-link attachments are not allowed.")
        target = target.resolve()
        if not target.is_relative_to(documents_root) or not target.is_file():
            raise ValueError(f"Attachment not found: {relative_path}")
        if any(part.startswith(".") for part in target.relative_to(documents_root).parts):
            raise ValueError("Hidden files cannot be attached.")
        data = target.read_bytes()
        total_size += len(data)
        if total_size > 20 * 1024 * 1024:
            raise ValueError("Combined attachments cannot exceed 20 MB.")
        import mimetypes
        content_type, _ = mimetypes.guess_type(target.name)
        maintype, subtype = (content_type or "application/octet-stream").split("/", 1)
        message.add_attachment(data, maintype=maintype, subtype=subtype, filename=target.name)

    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    return get_gmail_service().users().messages().send(
        userId="me", body={"raw": raw}
    ).execute()


def get_tasks_service():
    return build(
        "tasks",
        "v1",
        credentials=get_google_credentials(),
        cache_discovery=False,
    )


def validate_google_task_change(action, task_list_id, task_id=None, changes=None):
    """Validate the change and return its Google API request body."""
    from datetime import date

    if action not in ("create", "update", "delete"):
        raise ValueError("Unsupported Google Task action.")
    if not isinstance(task_list_id, str) or not task_list_id.strip():
        raise ValueError("A Google task list ID is required.")
    if action == "create":
        if task_id is not None:
            raise ValueError("A new task cannot have an existing task ID.")
    elif not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("An existing Google task ID is required.")

    if changes is None:
        changes = {}
    if not isinstance(changes, dict):
        raise ValueError("Task changes must be an object.")
    body = changes.copy()
    if body.keys() - {"title", "notes", "due", "status"}:
        raise ValueError("Unsupported Google Task field.")
    if action == "create" or "title" in body:
        if not isinstance(body.get("title"), str) or not body["title"].strip():
            raise ValueError("A task title is required.")
    if "notes" in body and not isinstance(body["notes"], str):
        raise ValueError("Task notes must be text.")
    if "status" in body and body["status"] not in ("needsAction", "completed"):
        raise ValueError("Invalid task completion status.")
    if "due" in body and body["due"] is not None:
        due = body["due"]
        try:
            day = date.fromisoformat(due)
        except (ValueError, TypeError):
            raise ValueError("Use YYYY-MM-DD for a task due date.") from None
        if day.isoformat() != due:
            raise ValueError("Use YYYY-MM-DD for a task due date.")
        body["due"] = due + "T00:00:00Z"
    if action == "update" and not body:
        raise ValueError("Select at least one task field to update.")
    if action == "delete" and body:
        raise ValueError("A task deletion cannot include field changes.")

    return body


def execute_approved_google_task(action, task_list_id, task_id=None, changes=None):
    body = validate_google_task_change(action, task_list_id, task_id, changes)
    credentials = get_google_credentials(require_tasks_write=True)
    tasks = build("tasks", "v1", credentials=credentials).tasks()
    if action == "create":
        return tasks.insert(tasklist=task_list_id, body=body).execute()
    if action == "update":
        return tasks.patch(tasklist=task_list_id, task=task_id, body=body).execute()
    return tasks.delete(tasklist=task_list_id, task=task_id).execute()


def list_google_task_lists(service):
    task_lists = []
    page_token = None
    while True:
        response = service.tasklists().list(
            maxResults=100, pageToken=page_token
        ).execute()
        task_lists.extend(response.get("items", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return task_lists


def list_google_tasks(query="", show_completed=True, max_results=100):
    service = get_tasks_service()
    task_lists = list_google_task_lists(service)
    results = []
    needle = str(query or "").strip().casefold()
    limit = max(1, min(int(max_results), 500))
    for task_list in task_lists:
        page_token = None
        while True:
            response = service.tasks().list(
                tasklist=task_list["id"],
                maxResults=100,
                showCompleted=bool(show_completed),
                showHidden=True,
                pageToken=page_token,
            ).execute()
            for task in response.get("items", []):
                title = task.get("title", "")
                notes = task.get("notes", "")
                if needle and needle not in (title + " " + notes).casefold():
                    continue
                results.append({
                    "id": task.get("id"),
                    "title": title,
                    "notes": notes,
                    "due": task.get("due"),
                    "status": task.get("status"),
                    "completed": task.get("completed"),
                    "updated": task.get("updated"),
                    "task_list_id": task_list.get("id"),
                    "task_list_title": task_list.get("title"),
                    "kind": "google_task",
                })
                if len(results) >= limit:
                    return results
            page_token = response.get("nextPageToken")
            if not page_token:
                break
    return results


def get_calendar_service():
    return build(
        "calendar",
        "v3",
        credentials=get_google_credentials(),
        cache_discovery=False,
    )


def google_calendar_status():
    if not TOKEN_FILE.exists():
        return {"connected": False}

    try:
        calendar = get_calendar_service()
        calendar.events().list(
            calendarId="primary",
            maxResults=1,
        ).execute()
        return {
            "connected": True,
            "calendar": "Primary calendar",
        }
    except Exception as error:
        return {"connected": False, "error": str(error)}


def create_calendar_event(
    summary,
    start_time="",
    end_time="",
    description="",
    location="",
    start=None,
    end=None,
    recurrence=None,
):
    if not summary.strip():
        raise ValueError("Event title is required.")

    if start is not None and end is not None:
        event = {"summary": summary.strip(), "start": start, "end": end}
    else:
        if not start_time or not end_time or end_time <= start_time:
            raise ValueError("A valid start and end date/time are required.")
        event = {
            "summary": summary.strip(),
            "start": {"dateTime": start_time},
            "end": {"dateTime": end_time},
        }

    if recurrence:
        event["recurrence"] = recurrence
    if description.strip():
        event["description"] = description.strip()
    if location.strip():
        event["location"] = location.strip()

    return get_calendar_service().events().insert(
        calendarId="primary",
        body=event,
    ).execute()


def disconnect_google_calendar():
    TOKEN_FILE.unlink(missing_ok=True)
    STATE_FILE.unlink(missing_ok=True)


def list_calendar_events(
    time_min="",
    time_max="",
    query="",
    max_results=50,
):
    params = {
        "calendarId": "primary",
        "maxResults": max(1, min(int(max_results), 100)),
        "singleEvents": True,
        "orderBy": "startTime",
    }
    if time_min:
        params["timeMin"] = time_min
    if time_max:
        params["timeMax"] = time_max
    if query:
        params["q"] = query

    result = get_calendar_service().events().list(**params).execute()
    return result.get("items", [])


def get_calendar_event(event_id):
    return get_calendar_service().events().get(
        calendarId="primary",
        eventId=event_id,
    ).execute()


def update_calendar_event(event_id, updates):
    if not updates:
        raise ValueError("At least one event field must be updated.")

    allowed = {"summary", "description", "location", "start", "end", "recurrence"}
    if set(updates) - allowed:
        raise ValueError("Unsupported calendar event field.")

    # For one-off timed events, send the explicit RFC3339 offset without
    # a separate IANA timezone. This avoids timezone-database conflicts
    # when converting an existing all-day event into a timed event.
    body = dict(updates)
    for field in ("start", "end"):
        value = body.get(field)
        if isinstance(value, dict) and value.get("dateTime"):
            body[field] = dict(value)
            body[field].pop("timeZone", None)

    return get_calendar_service().events().patch(
        calendarId="primary",
        eventId=event_id,
        body=body,
    ).execute()


def delete_calendar_event(event_id):
    get_calendar_service().events().delete(
        calendarId="primary",
        eventId=event_id,
    ).execute()
    return {"id": event_id, "deleted": True}
