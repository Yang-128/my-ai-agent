import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from runtime import DATA_DIR

PENDING_FILE = DATA_DIR / "pending_calendar_events.json"
_LOCK = threading.Lock()
_EXPIRY = timedelta(minutes=30)


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
    temporary.replace(PENDING_FILE)


def create_pending_calendar_action(arguments, conversation_id, action):
    if action not in {"create", "update", "delete"}:
        raise ValueError("Unsupported calendar action.")

    event_id = str(arguments.get("event_id", "")).strip()
    if action in {"update", "delete"} and not event_id:
        raise ValueError("An existing event ID is required.")

    event = {}
    if action in {"create", "update"}:
        summary = str(
            arguments.get("summary")
            or arguments.get("title")
            or arguments.get("task")
            or arguments.get("name")
            or "Untitled event"
        ).strip()
        start_time = str(arguments.get("start_time", "")).strip()
        end_time = str(arguments.get("end_time", "")).strip()
        start_date = str(arguments.get("start_date", "")).strip()
        end_date = str(arguments.get("end_date", "")).strip()
        location = str(arguments.get("location", "")).strip()
        description = str(arguments.get("description", "")).strip()
        recurrence = arguments.get("recurrence", [])

        if isinstance(recurrence, str):
            recurrence = [recurrence] if recurrence.strip() else []
        if not isinstance(recurrence, list) or not all(
            isinstance(rule, str) and rule.startswith("RRULE:") for rule in recurrence
        ):
            raise ValueError("Recurrence must be a list of RRULE strings.")

        if len(summary) > 200:
            raise ValueError("Event title must be 200 characters or fewer.")

        if summary:
            event["summary"] = summary
        if location:
            event["location"] = location[:500]
        if description:
            event["description"] = description[:5000]

        local_timezone = ZoneInfo("America/Vancouver")
        if start_date or end_date:
            if not start_date or not end_date:
                raise ValueError("Both start and end dates are required for an all-day event.")
            try:
                start_day = datetime.strptime(start_date, "%Y-%m-%d").date()
                end_day = datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("All-day dates must use YYYY-MM-DD.")
            if start_day.isoformat() != start_date or end_day.isoformat() != end_date:
                raise ValueError("All-day dates must use YYYY-MM-DD.")
            if end_day <= start_day:
                raise ValueError("End date must be after start date. For a one-day event, use the following day as the end date.")
            if start_time or end_time:
                raise ValueError("An event cannot mix all-day dates with date-times.")
            event["start"] = {"date": start_date}
            event["end"] = {"date": end_date}
        else:
            if start_time:
                try:
                    start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError("Start time must be a valid ISO 8601 date-time.")
                start = start.replace(tzinfo=local_timezone) if start.tzinfo is None else start.astimezone(local_timezone)
                event["start"] = {"dateTime": start.isoformat(), "timeZone": "America/Vancouver"}

            if end_time:
                try:
                    end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
                except ValueError:
                    raise ValueError("End time must be a valid ISO 8601 date-time.")
                end = end.replace(tzinfo=local_timezone) if end.tzinfo is None else end.astimezone(local_timezone)
                event["end"] = {"dateTime": end.isoformat(), "timeZone": "America/Vancouver"}

            if "start" in event and "end" in event:
                if datetime.fromisoformat(event["end"]["dateTime"]) <= datetime.fromisoformat(event["start"]["dateTime"]):
                    raise ValueError("Event end time must be later than its start time.")

        if recurrence:
            event["recurrence"] = recurrence

    pending_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    with _LOCK:
        data = _read()
        data = {
            key: item for key, item in data.items()
            if datetime.fromisoformat(item["created_at"]) > now - _EXPIRY
        }
        proposal = {"action": action, "event_id": event_id, "event": event}
        data[pending_id] = {
            "proposal": proposal,
            "conversation_id": str(conversation_id),
            "created_at": now.isoformat(),
        }
        _write(data)

    return {"pending_id": pending_id, **proposal}

def create_pending_calendar_actions(actions, conversation_id):
    if not actions:
        raise ValueError("At least one calendar change is required.")
    if len(actions) > 50:
        raise ValueError("A maximum of 50 calendar changes can be proposed at once.")

    created = []
    try:
        for arguments, action in actions:
            created.append(
                create_pending_calendar_action(arguments, conversation_id, action)
            )
    except Exception:
        for item in created:
            discard_pending_calendar_event(item["pending_id"])
        raise

    return {
        "pending_ids": [item["pending_id"] for item in created],
        "proposals": created,
    }



def update_pending_calendar_event_times(
    pending_id, start_time="", end_time="", start_date="", end_date="",
    summary=None, description=None, location=None
):
    start_time = str(start_time or "").strip()
    end_time = str(end_time or "").strip()
    start_date = str(start_date or "").strip()
    end_date = str(end_date or "").strip()

    with _LOCK:
        data = _read()
        item = data.get(str(pending_id))
        if not item:
            raise ValueError("This event proposal has expired or has already been handled.")
        created = datetime.fromisoformat(item["created_at"])
        if datetime.now(timezone.utc) - created > _EXPIRY:
            raise ValueError("This event proposal has expired. Ask the assistant to propose it again.")

        proposal = item.get("proposal", {})
        action = proposal.get("action")
        if action not in {"create", "update"}:
            raise ValueError("Times can only be set for create or update proposals.")

        event = proposal.setdefault("event", {})

        if summary is not None:
            summary = str(summary).strip()
            if not summary:
                raise ValueError("Event title cannot be empty.")
            if len(summary) > 200:
                raise ValueError("Event title cannot exceed 200 characters.")
            event["summary"] = summary

        if description is not None:
            event["description"] = str(description).strip()[:5000]

        if location is not None:
            event["location"] = str(location).strip()[:500]

        if start_date or end_date:
            if not start_date or not end_date:
                raise ValueError("Both start and end dates are required for an all-day event.")
            try:
                start_day = datetime.strptime(start_date, "%Y-%m-%d").date()
                end_day = datetime.strptime(end_date, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("All-day dates must use YYYY-MM-DD.")
            if start_day.isoformat() != start_date or end_day.isoformat() != end_date:
                raise ValueError("All-day dates must use YYYY-MM-DD.")
            if end_day <= start_day:
                raise ValueError("End date must be after start date. For a one-day event, use the following day as the end date.")
            if start_time or end_time:
                raise ValueError("An event cannot mix all-day dates with date-times.")
            event["start"] = {"date": start_date}
            event["end"] = {"date": end_date}
            _write(data)
            return True

        if not start_time and not end_time:
            if action == "create" and not (
                event.get("start", {}).get("date")
                and event.get("end", {}).get("date")
                or event.get("start", {}).get("dateTime")
                and event.get("end", {}).get("dateTime")
            ):
                raise ValueError("Please enter both a start and end date/time.")
            _write(data)
            return True

        if not start_time or not end_time:
            raise ValueError("Both start and end date/time are required.")

        local_timezone = ZoneInfo("America/Vancouver")
        try:
            start = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            end = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("Start and end must be valid date/time values.")

        start = start.replace(tzinfo=local_timezone) if start.tzinfo is None else start.astimezone(local_timezone)
        end = end.replace(tzinfo=local_timezone) if end.tzinfo is None else end.astimezone(local_timezone)
        if end <= start:
            raise ValueError("Event end time must be later than its start time.")

        event["start"] = {"dateTime": start.isoformat(), "timeZone": "America/Vancouver"}
        event["end"] = {"dateTime": end.isoformat(), "timeZone": "America/Vancouver"}
        _write(data)

    return True

def get_pending_calendar_event(pending_id):
    with _LOCK:
        item = _read().get(str(pending_id))

    if not item:
        raise ValueError("This event proposal has expired or has already been handled.")

    created = datetime.fromisoformat(item["created_at"])
    if datetime.now(timezone.utc) - created > _EXPIRY:
        discard_pending_calendar_event(pending_id)
        raise ValueError("This event proposal has expired. Ask the assistant to propose it again.")

    return item


def discard_pending_calendar_event(pending_id):
    with _LOCK:
        data = _read()
        existed = data.pop(str(pending_id), None) is not None
        _write(data)
    return existed
