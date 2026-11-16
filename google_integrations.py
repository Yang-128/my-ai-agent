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
SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

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

    if not credentials.refresh_token:
        raise RuntimeError(
            "Google did not provide a refresh token. Revoke this app's access "
            "in your Google Account and connect again."
        )

    TOKEN_FILE.write_text(credentials.to_json())
    os.chmod(TOKEN_FILE, 0o600)
    STATE_FILE.unlink(missing_ok=True)


def get_google_credentials():
    if not TOKEN_FILE.exists():
        raise RuntimeError("Google Calendar is not connected.")

    credentials = Credentials.from_authorized_user_file(
        str(TOKEN_FILE),
        scopes=SCOPES,
    )

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        TOKEN_FILE.write_text(credentials.to_json())
        os.chmod(TOKEN_FILE, 0o600)

    if not credentials.valid:
        raise RuntimeError("Google authorization expired. Reconnect Google Calendar.")

    return credentials


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

    return get_calendar_service().events().patch(
        calendarId="primary",
        eventId=event_id,
        body=updates,
    ).execute()


def delete_calendar_event(event_id):
    get_calendar_service().events().delete(
        calendarId="primary",
        eventId=event_id,
    ).execute()
    return {"id": event_id, "deleted": True}
