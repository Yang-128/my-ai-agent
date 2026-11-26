import json
import base64
import binascii
import re
import traceback
from pathlib import Path
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from storage import (
    get_conversation,
    save_message,
    record_usage,
    record_turn_usage,
    record_provider_request,
    get_tool_settings,
)
from tools import get_all_tool_definitions, execute_tool
from providers import get_provider_client, get_provider_model
from school_timetable import weekly_school_actions
from calendar_approval import create_pending_calendar_actions
from task_approval import only_list_task_title, prepare_google_task_proposal, prepare_only_list_create
from gmail_approval import create_pending_gmail_message


router = APIRouter()


def claims_task_proposal(text):
    """Detect a model asking for approval without producing a tool call."""
    return bool(re.search(
        r"\b(?:prepared|created|ready|submitted)\b.{0,80}\bproposal\b"
        r"|\bproposal\b.{0,50}\b(?:ready|prepared|awaiting approval)\b"
        r"|\b(?:please review and approve|awaiting your approval|approve the change below)\b",
        text or "", re.IGNORECASE | re.DOTALL,
    ))


def execute_chat_tool(name, arguments, user_message, reference_time=None):
    arguments = dict(arguments)

    if name == "mcp_search_calendar_events":
        weekdays = {
            "monday": 0,
            "tuesday": 1,
            "wednesday": 2,
            "thursday": 3,
            "friday": 4,
            "saturday": 5,
            "sunday": 6,
        }
        match = re.search(
            r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
            user_message.lower(),
        )
        if match:
            zone = ZoneInfo("America/Vancouver")
            timestamp_match = re.search(
                r"\((\d{4}-\d{2}-\d{2}T[^)]+)\)",
                str(reference_time or ""),
            )
            if not timestamp_match:
                raise ValueError("Authoritative calendar time is unavailable.")
            today = datetime.fromisoformat(
                timestamp_match.group(1)
            ).astimezone(zone).date()
            target_weekday = weekdays[match.group(1)]
            monday = today - timedelta(days=today.weekday())
            target_date = monday + timedelta(days=7 + target_weekday)

            start = datetime.combine(target_date, time.min, tzinfo=zone)
            end = start + timedelta(days=1)
            arguments["time_min"] = start.isoformat()
            arguments["time_max"] = end.isoformat()
            arguments["query"] = ""

            print(
                f"[CALENDAR DATE] Resolved 'next {match.group(1)}' "
                f"to {target_date.isoformat()} ({match.group(1)}); "
                f"searching {start.isoformat()} to {end.isoformat()}",
                flush=True,
            )

    return execute_tool(name, arguments)


def format_provider_error(error):
    """Extract the actual provider/API failure reason."""
    response = getattr(error, "response", None)
    status = getattr(error, "status_code", None) or getattr(response, "status_code", None)
    payload = getattr(error, "body", None)

    if payload is None and response is not None:
        try:
            payload = response.json()
        except Exception:
            payload = getattr(response, "text", None)

    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            pass

    reason = None
    if isinstance(payload, dict):
        details = payload.get("error", payload)
        if isinstance(details, dict):
            for key in ("message", "detail", "description", "reason"):
                value = details.get(key)
                if isinstance(value, str) and value.strip():
                    reason = value.strip()
                    break
            if not reason:
                reason = json.dumps(details, ensure_ascii=False)
        elif isinstance(details, str):
            reason = details.strip()
    elif isinstance(payload, str) and payload.strip():
        reason = payload.strip()

    if not reason:
        reason = str(error).strip() or type(error).__name__

    prefix = f"Provider request failed (HTTP {status})" if status else f"Provider request failed ({type(error).__name__})"
    return f"{prefix}: {reason}"


class ChatRequest(BaseModel):
    conversation_id: str
    message: str
    model: str = ""
    provider: str = "groq"
    selected_tools: list[str] | None = None
    images: list[str] = []
    retry_without_saving: bool = False


def validate_images(images):
    if len(images) > 4:
        raise HTTPException(status_code=413, detail="Maximum 4 images per message.")

    validated = []
    total_size = 0
    signatures = {
        "image/png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
        "image/webp": lambda data: (
            len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
        ),
    }

    for image in images:
        if not isinstance(image, str) or "," not in image:
            raise HTTPException(status_code=400, detail="Invalid image data.")

        header, encoded = image.split(",", 1)
        mime = header.removeprefix("data:").removesuffix(";base64")

        if header != f"data:{mime};base64" or mime not in signatures:
            raise HTTPException(
                status_code=415,
                detail="Only PNG, JPEG, and WEBP images are supported."
            )

        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            raise HTTPException(status_code=400, detail="Invalid image encoding.")

        if not raw or len(raw) > 6 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Each image must be 6 MB or smaller.")

        if not signatures[mime](raw):
            raise HTTPException(status_code=400, detail="Image content does not match its file type.")

        total_size += len(raw)
        if total_size > 16 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Combined image size exceeds 16 MB.")

        validated.append(image)

    return validated


def _int_header(headers, name):
    try:
        value = headers.get(name)
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def save_provider_headers(provider, model, headers):
    if provider == "groq":
        record_usage(
            provider,
            model,
            requests_limit=_int_header(headers, "x-ratelimit-limit-requests"),
            requests_remaining=_int_header(headers, "x-ratelimit-remaining-requests"),
            requests_reset=headers.get("x-ratelimit-reset-requests"),
            tokens_limit=_int_header(headers, "x-ratelimit-limit-tokens"),
        )


def ai_decide_extensions(message, provider, model, available_tools, conversation_context=None):
    """Select MCP extensions using the latest message and recent conversation context."""
    extensions = []

    for tool in available_tools:
        definition = tool.get("function", {})
        name = definition.get("name", "")

        if not name.startswith("mcp_"):
            continue

        extensions.append({
            "name": name,
            "description": definition.get("description", ""),
            "parameters": definition.get("parameters", {}),
        })

    if not extensions:
        return {
            "needs_extensions": False,
            "required_extensions": [],
            "reason": "No extensions are available.",
        }

    if only_list_task_title(message) is not None:
        names = {extension["name"] for extension in extensions}
        required = ["mcp_list_google_task_lists", "mcp_propose_google_task_change"]
        if all(name in names for name in required):
            return {
                "needs_extensions": True,
                "required_extensions": required,
                "reason": "Identify your only task list and prepare the task for approval.",
            }

    client = get_provider_client(provider)

    system_prompt = (
        "You are the tool-planning component of a personal AI agent. Select "
        "the complete, minimal set of MCP extensions the main assistant needs "
        "to fulfill the latest request in context. Treat this as dependency "
        "analysis, not keyword matching. Identify the task and its required "
        "steps, then map every step to the exact available tool whose "
        "description and parameters support it. Include prerequisite and "
        "supporting tools, not just the final action tool. For example, if the "
        "main assistant's instructions require obtaining the current local "
        "date before interpreting relative dates, include the current-time "
        "tool even if the user did not explicitly ask for the time. If a task "
        "requires searching before updating, include both the search/read "
        "tool and the update tool. For multiple calendar entries, prefer a "
        "batch creation tool when available. Include tools needed to verify, "
        "retrieve details, or resolve references when the workflow requires "
        "them. Do not include unrelated tools from the same service. "
        "Use recent conversation context to interpret short follow-ups such "
        "as yes, update, do it, move it, that one, or continue. Never assess "
        "a follow-up in isolation when relevant context exists. Read tool "
        "descriptions carefully. Select only exact names from the supplied "
        "list; never invent names or assume a tool exists. Prefer a complete "
        "set over an incomplete one, but keep it minimal. If no MCP tool is "
        "needed, return an empty list. "
        "Return only valid JSON with this exact structure: "
        '{"needs_extensions": true, '
        '"required_extensions": ["exact_tool_name"], '
        '"reason": "brief explanation of the task and dependencies"}. '
        "When no extension is needed, return false and an empty list."
    )

    recent_context = []
    for item in (conversation_context or [])[-10:]:
        role = item.get("role", "")
        content = str(item.get("content", ""))
        if role in {"user", "assistant"} and content.strip():
            recent_context.append({
                "role": role,
                "content": content[-3000:],
            })

    user_prompt = (
        "Recent conversation (oldest first):\n"
        + json.dumps(recent_context, ensure_ascii=False)
        + "\n\nLatest user message:\n"
        + message
        + "\n\nAvailable extensions:\n"
        + json.dumps(extensions, ensure_ascii=False)
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=700,
        temperature=0,
    )

    content = response.choices[0].message.content or ""
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())

    try:
        decision = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        match = re.search(r"\{.*\}", content, re.S)
        if not match:
            raise ValueError("The model returned an invalid extension decision.")
        decision = json.loads(match.group(0))

    available_names = {item["name"] for item in extensions}
    requested_names = decision.get("required_extensions", [])

    if not isinstance(requested_names, list):
        requested_names = []

    required = list(dict.fromkeys(
        name for name in requested_names
        if isinstance(name, str) and name in available_names
    ))

    # Deterministically select web tools for explicit research and URL requests.
    message_lower = message.lower()
    has_url = bool(re.search(r"https?://[^\s<>()]+|www\.[^\s<>()]+", message_lower))

    web_search_patterns = (
        r"\b(?:search the web|web search|browse the web|browse online|search online|look online|check online|research online|research the web|investigate online|online research|on the internet|find online)\b",
        r"\bsearch for (?:information|sources|articles|documentation|news|updates)\b",
        r"\bfind (?:me )?(?:reliable )?(?:sources|citations|references)\b",
        r"\blook up (?:the )?(?:latest|current|most recent|up-to-date)\b",
        r"\b(?:latest|current|most recent|up-to-date)\b.{0,80}\b(?:news|price|version|release|policy|law|regulation|status|schedule|events|developments|information|data|research)\b",
        r"\b(?:what is|what's|tell me)\s+(?:the )?(?:latest|current|most recent)\b",
        r"\b(?:today|this week|this month|right now)\b.{0,60}\b(?:news|updates|prices|status|results|developments)\b",
    )
    web_search_intent = any(
        re.search(pattern, message_lower)
        for pattern in web_search_patterns
    )

    if web_search_intent and "mcp_web_search" in available_names:
        if "mcp_web_search" not in required:
            required.append("mcp_web_search")

    if (has_url or web_search_intent) and "mcp_web_fetch" in available_names:
        if "mcp_web_fetch" not in required:
            required.append("mcp_web_fetch")

    # Calendar requests always need the authoritative clock. Searching or
    # changing events also requires the calendar search tool; changes require
    # the corresponding write tool and event details when available.
    calendar_intent = any(term in message.lower() for term in (
        "calendar", "event", "appointment", "meeting", "schedule",
        "physics test", "test next", "next monday", "next tuesday",
        "next wednesday", "next thursday", "next friday",
        "next saturday", "next sunday", "tomorrow", "today",
    ))
    if calendar_intent:
        calendar_tools = [
            "mcp_get_current_time",
            "mcp_search_calendar_events",
            "mcp_get_calendar_event_details",
        ]
        if any(term in message.lower() for term in (
            "update", "change", "move", "reschedule", "delete", "cancel",
            "create", "add", "schedule", "put",
        )):
            calendar_tools.extend((
                "mcp_update_calendar_event",
                "mcp_delete_calendar_event",
                "mcp_create_calendar_events",
            ))

        for name in calendar_tools:
            if name in available_names and name not in required:
                required.append(name)

    # Calendar-related requests should inspect both event and task records.
    if calendar_intent:
        if "mcp_search_google_tasks" in available_names and "mcp_search_google_tasks" not in required:
            required.append("mcp_search_google_tasks")

    # Explicit task requests also require the authoritative local clock.
    task_intent = any(term in message.lower() for term in (
        "task", "tasks", "todo", "to-do", "to do", "checklist", "reminder",
    ))
    if task_intent:
        for name in (
            "mcp_search_google_tasks",
            "mcp_list_google_task_lists",
            "mcp_propose_google_task_change",
        ):
            if name in available_names and name not in required:
                required.append(name)

    if task_intent and "mcp_get_current_time" in available_names and "mcp_get_current_time" not in required:
        required.append("mcp_get_current_time")

    # Web research requires both discovery and page extraction.
    if "mcp_web_search" in required and "mcp_web_fetch" in available_names:
        if "mcp_web_fetch" not in required:
            required.append("mcp_web_fetch")

    # Ensure explicit document and email requests include their required tools.
    message_lower = message.lower()
    document_intent = (
        any(word in message_lower for word in (
            "create", "make", "generate", "prepare", "produce", "export"
        ))
        and any(word in message_lower for word in (
            "document", "pdf", "report", "spreadsheet", "file", "xlsx",
            "docx", "pptx"
        ))
    )
    if document_intent and "mcp_mac_create_document" in available_names:
        if "mcp_mac_create_document" not in required:
            required.append("mcp_mac_create_document")

    email_intent = (
        any(word in message_lower for word in ("email", "gmail"))
        or "send it to" in message_lower
        or "send this to" in message_lower
    )
    if email_intent and "mcp_send_gmail_message" in available_names:
        if "mcp_send_gmail_message" not in required:
            required.append("mcp_send_gmail_message")

    return {
        "needs_extensions": bool(required),
        "required_extensions": required,
        "reason": str(decision.get("reason", ""))[:500],
    }


class ExtensionDecisionRequest(BaseModel):
    message: str
    conversation_id: str = ""
    provider: str = "groq"
    model: str = ""


@router.post("/api/ai-decide-extensions")
def decide_extensions(request: ExtensionDecisionRequest):
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    try:
        model = request.model or get_provider_model(request.provider)
        settings = get_tool_settings()
        available = [tool for tool in get_all_tool_definitions()
                     if settings.get(tool["function"]["name"], True)]
        conversation = (
            get_conversation(request.conversation_id)
            if request.conversation_id else None
        )
        history = conversation.get("messages", []) if conversation else []
        return ai_decide_extensions(
            message, request.provider, model, available, history
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as error:
        detail = format_provider_error(error)
        print(f"Extension assessment failed [{request.provider}/{request.model}]: {detail}", flush=True)
        raise HTTPException(
            status_code=502,
            detail=f"Extension assessment failed: {detail}"
        )


@router.post("/api/chat")
def chat(request: ChatRequest):
    conversation = get_conversation(request.conversation_id)

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    message = request.message.strip()
    images = validate_images(request.images)

    if not message and not images:
        raise HTTPException(status_code=400, detail="Message or image is required.")

    if images:
        from providers import get_available_models
        available = get_available_models(request.provider)
        model_info = next(
            (item for item in available
             if item["id"] == (request.model or get_provider_model(request.provider))),
            None,
        )
        if not model_info or not model_info.get("capabilities", {}).get("image"):
            raise HTTPException(
                status_code=400,
                detail="The selected model does not support image input. Please choose a vision-capable model.",
            )

    try:
        client = get_provider_client(request.provider)
        selected_model = request.model or get_provider_model(request.provider)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error))

    if not request.retry_without_saving:
        save_message(
            request.conversation_id,
            "user",
            message,
            images=images,
        )
    updated = get_conversation(request.conversation_id)

    messages = []
    for item in updated["messages"]:
        content = item["content"]
        if item.get("images"):
            content = []
            if item["content"]:
                content.append({"type": "text", "text": item["content"]})
            content.extend(
                {
                    "type": "image_url",
                    "image_url": {"url": image},
                }
                for image in item["images"]
            )
        messages.append({"role": item["role"], "content": content})

    def generate():
        full_reply = ""
        turn_input_tokens = 0
        turn_output_tokens = 0
        has_turn_usage = False

        try:
            tool_settings = get_tool_settings()
            requested_tools = (
                set(request.selected_tools)
                if request.selected_tools is not None
                else None
            )
            all_tool_definitions = get_all_tool_definitions()
            available_tools = [
                tool for tool in all_tool_definitions
                if tool_settings.get(tool["function"]["name"], True)
                and (
                    requested_tools is None
                    or tool["function"]["name"] in requested_tools
                )
            ]
            tool_calling_supported = bool(available_tools)

            try:
                calendar_reference_time = execute_tool("mcp_get_current_time", {})
            except Exception as error:
                calendar_reference_time = f"UNAVAILABLE: {error}"

            system_message = {
                "role": "system",
                "content": (
                    "You are a helpful personal AI assistant. "
                    "Use available tools when useful. "
                    "AUTHORITATIVE CURRENT LOCAL DATE/TIME: "
                    + calendar_reference_time
                    + ". This value is injected by the application for this request. "
                    "Use it as the source of truth for all relative dates. "
                    "You can use MCP tools to interact with connected "
                    "services and files. "
                    "CALENDAR DATE INTERPRETATION RULES: The authoritative "
                    "current local date and time are injected above by the "
                    "application in America/Vancouver time. Use that value for "
                    "all date calculations; do not rely on the model's assumed date. "
                    "Resolve relative dates with calendar arithmetic and verify "
                    "that every calculated date matches its stated weekday. "
                    "'Next Tuesday' means Tuesday in the next calendar week. "
                    "For example, when today is Monday 2026-11-16, next Tuesday "
                    "is 2026-11-24, not 2026-11-17. A weekday without 'next' "
                    "means its next occurrence strictly after today, unless the "
                    "user clearly means today. Never invent or silently change "
                    "a date. If wording is ambiguous, ask for clarification. "
                    "Interpret 'by Monday' as a deadline on Monday. Interpret "
                    "'end of this week' as Friday of the current Monday-to-Friday "
                    "week. Preserve a stated year; if no year is given for a "
                    "month/day date, choose its next upcoming occurrence. "
                    "For phrases such as 'every Monday', 'every Monday, Friday and "
                    "Saturday', or 'Tuesdays after school', recognize that these are "
                    "recurring schedules, not single events. Do not silently turn "
                    "them into one-time events or invent times. If recurrence or "
                    "event times cannot be represented by the available calendar "
                    "tools, ask a concise clarification instead of pretending the "
                    "schedule has been fully captured. "
                    "For deadlines and birthdays without a specified time, create "
                    "all-day events using start_date and end_date in YYYY-MM-DD format; "
                    "end_date is exclusive, so use the following calendar day. Do not "
                    "invent a time. For recurring events, use Google Calendar RRULE "
                    "strings in a recurrence array, for example "
                    "RRULE:FREQ=WEEKLY;BYDAY=MO,FR,SA. Do not invent a recurrence end "
                    "date; omit UNTIL and COUNT unless the user specifies one. "
                    "For Google Calendar, use "
                    "mcp_search_calendar_events and mcp_get_calendar_event_details "
                    "to inspect events before answering questions or proposing changes. "
                    "When searching for events on a particular day, search the full local "
                    "calendar-day interval: from that day's local midnight to the next "
                    "day's local midnight, using ISO 8601 timestamps with timezone offsets. "
                    "Do not search only a narrow time window when the user gives a day. "
                    "Search by date range without a title query first; use a title query "
                    "only as an additional search if needed. Google Calendar all-day events "
                    "use start.date and end.date, not start.dateTime and end.dateTime. "
                    "Always inspect both date-only and dateTime fields when matching events. "
                    "For all-day events, start.date is inclusive and end.date is "
                    "exclusive. An all-day event overlaps the requested date when "
                    "start.date is on or before that date and end.date is after it; "
                    "do not require start.date to equal the requested date, because "
                    "multi-day events may have started earlier. Do not reject an event "
                    "because it has no dateTime or clock time. "
                    "Google Calendar events and Google Tasks are different object types. "
                    "A calendar event has a scheduled start/end time or all-day date; "
                    "a Google Task is a task-list item with its own completion status "
                    "and optional due date. Never describe a Google Task as a calendar "
                    "event or assume that a task appears in calendar event search. "
                    "When the user asks what tasks exist, search Google Tasks using "
                    "mcp_search_google_tasks. When the user asks what is scheduled, "
                    "inspect both calendar events and Google Tasks when available, "
                    "and label the two types distinctly in the answer. A task due date "
                    "does not imply a scheduled time. Do not invent a time for a task. "
                    "Use mcp_create_calendar_events for ALL event creation requests, "
                    "including a single event, using an events array. Each item must "
                    "include action='create' and the relevant event fields. Use "
                    "mcp_update_calendar_event and mcp_delete_calendar_event for "
                    "changes to existing events. IMPORTANT: When the user asks to split "
                    "or separate one existing event into multiple distinct events, do not "
                    "merely rename or modify the original event. First identify and inspect "
                    "the original event. Then call mcp_create_calendar_events exactly once "
                    "with an events array containing an action='update' item for the "
                    "original event and an action='create' item for every additional event. "
                    "Preserve the original event ID on the update item. Give every resulting "
                    "event its own requested title, start, and end. Verify that the batch "
                    "contains every requested event before proposing it for approval. Never "
                    "treat a split as complete if the batch contains only the update. "
                    "You may propose additional "
                    "batches in later turns of the same conversation; there is no one-batch "
                    "limit per conversation. Every change must be presented together for "
                    "explicit user approval in chat before execution. Never claim a change "
                    "has happened before approval. "
                    "WEB SEARCH AND SOURCE EXTRACTION RULES: When the user asks for current information or web research, use mcp_web_search to find sources. Prefer official websites and primary sources. Use site:domain.com queries when the user specifies a website or organization. Then call mcp_web_fetch on relevant result URLs to inspect the actual page content. Follow useful links returned by web_fetch, including PDF calendars and documents, by calling web_fetch on those URLs. Do not rely only on search snippets when the source page can be opened. For school calendars, extract the actual dates and event names, distinguish holidays and no-school days from other events, and preserve the school year. Never infer dates that are not supported by the retrieved source. Include clickable source URLs in your answer and identify when a source could not be accessed. Explain what you did clearly. "
                    "You may use conversation history provided in the "
                    "messages to maintain context. Never claim to remember "
                    "conversations that are not included in the messages. "
                    "When asked about previous conversations, use "
                    "mcp_search_conversations to find relevant discussions, "
                    "then mcp_get_conversation_history to retrieve details. "
                    "Base claims about past discussions on retrieved results. "
                    "When the user asks you to create, generate, export, or save a document, "
                    "use mcp_mac_create_document to create the actual file on this Mac. "
                    "Choose the format based on the user's request: DOCX for editable documents, "
                    "PDF for finished documents, XLSX for spreadsheets, PPTX for presentations, "
                    "CSV for tabular data, and TXT/MD/HTML when appropriate. "
                    "For XLSX, provide valid JSON content with a sheets array; each sheet has "
                    "a name and rows array. For PPTX, provide valid JSON content with a slides "
                    "array; each slide has a title and body. For other formats, provide the "
                    "document text as content. Use the default save folder unless the user "
                    "specifies another folder. Never claim a file was saved unless the tool "
                    "returns a successful result. Tell the user the exact saved path. "
                    "For Google Task changes, first resolve the list using "
                    "mcp_list_google_task_lists or mcp_search_google_tasks. For updates "
                    "and deletions, take task_id and task_list_id from the same search "
                    "result. Use conversation context to identify the intended task. "
                    "If the task or list is ambiguous, ask the user to choose. "
                    "Call mcp_propose_google_task_change by itself after lookups finish. "
                    "Propose one task at a time, preserve unspecified fields, and use "
                    "date-only due dates. Completion is an update with status completed. "
                    "Only report completion after the approval endpoint confirms it. "
                    "EMAIL AND ATTACHMENT RULES: When the user asks to email a document, "
                    "first create the requested document with mcp_mac_create_document. "
                    "Wait for that tool result before calling mcp_send_gmail_message; "
                    "never call document creation and email preparation in the same tool-call "
                    "batch. Include the created file in the email tool's attachments array, "
                    "using its path relative to ~/Documents/My AI Agent. If the user asks "
                    "for today's date in the subject or message, call mcp_get_current_time "
                    "first and use its returned local date. Never guess the date or use a "
                    "stale date. The email tool prepares a draft only. The user must be able "
                    "to edit To, Cc, Bcc, Reply-To, Subject, Body, and attachments, then "
                    "explicitly approve sending. Never claim an email was sent before approval."
                ),
            }

            history_intent = re.search(
                r"\b(previous|prior|earlier|past|last)\s+"
                r"(conversation|chat|discussion)|"
                r"\b(we|you)\s+(discussed|talked about|mentioned)|"
                r"\bwhat did we discuss\b|\bremember when\b",
                message,
                re.I,
            )

            if history_intent:
                stop_words = {
                    "previous", "prior", "earlier", "past", "last",
                    "conversation", "conversations", "discussion",
                    "discussed", "talked", "about", "mentioned",
                    "what", "were", "the", "did", "have", "with",
                    "from", "that", "this", "when", "remember",
                    "can", "you", "find", "tell", "show", "recall",
                    "our", "your", "for", "and", "was", "how",
                    "much", "many", "which", "where", "who",
                }

                terms = re.findall(r"[A-Za-z0-9.-]+", message)
                search_terms = list(dict.fromkeys(
                    term.lower() for term in terms
                    if len(term) >= 3 and term.lower() not in stop_words
                ))
                search_terms.sort(key=len, reverse=True)

                conversation_ids = []
                search_outputs = []

                try:
                    if search_terms:
                        search_query = " ".join(search_terms[:12])
                        search_result = execute_tool(
                            "mcp_search_conversations",
                            {"query": search_query, "limit": 5},
                        )
                        result_text = str(search_result)

                        if result_text and "No matching" not in result_text:
                            search_outputs.append(result_text)

                            for conversation_id in re.findall(
                                r"Conversation ID:\s*([^\s]+)",
                                result_text,
                            ):
                                if conversation_id not in conversation_ids:
                                    conversation_ids.append(conversation_id)

                    if conversation_ids:
                        histories = []

                        for conversation_id in conversation_ids[:2]:
                            history = execute_tool(
                                "mcp_get_conversation_history",
                                {
                                    "conversation_id": conversation_id,
                                    "limit": 20,
                                    "query": search_query,
                                },
                            )
                            if history and "Conversation not found" not in str(history):
                                histories.append(str(history))

                        if histories:
                            system_message["content"] += (
                                "\n\nFull previous conversation histories "
                                "retrieved for this request. Use these as evidence:\n\n"
                                + "\n\n---\n\n".join(histories)
                            )
                        else:
                            system_message["content"] += (
                                "\n\nMatching conversations were found, "
                                "but their histories could not be retrieved."
                            )
                    elif search_outputs:
                        system_message["content"] += (
                            "\n\nPrevious conversation search results "
                            "and excerpts:\n\n"
                            + "\n\n".join(search_outputs[:3])
                        )
                    else:
                        system_message["content"] += (
                            "\n\nNo matching previous conversation records "
                            "were found."
                        )

                except Exception as error:
                    print(f"Conversation retrieval warning: {error}")
                    system_message["content"] += (
                        "\n\nPrevious conversation retrieval failed."
                    )

            result = None
            calendar_proposal = None
            task_proposal = None
            retry_task_proposal = False
            task_change_requested = bool(
                re.search(r"\b(?:task|tasks|to-do|todo)\b", message, re.I)
                and re.search(
                    r"\b(?:create|add|update|edit|rename|move|mark|complete|delete|remove)\b",
                    message, re.I,
                )
            )
            gmail_proposal = None
            max_tool_rounds = 8

            if any(tool["function"]["name"] == "mcp_create_calendar_events" for tool in available_tools):
                try:
                    timetable = weekly_school_actions(message, datetime.now(ZoneInfo("America/Vancouver")))
                    if timetable:
                        calendar_proposal = create_pending_calendar_actions(timetable, request.conversation_id)
                except ValueError as error:
                    calendar_proposal = {"error": str(error)}

            enabled_names = {tool["function"]["name"] for tool in available_tools}
            if {"mcp_list_google_task_lists", "mcp_propose_google_task_change"} <= enabled_names:
                try:
                    task_proposal = prepare_only_list_create(message, request.conversation_id)
                except Exception as error:
                    task_proposal = {"error": str(error)}

            for round_number in range(max_tool_rounds + 1):
                if calendar_proposal or task_proposal:
                    break
                request_args = {
                    "model": selected_model,
                    "messages": [system_message, *messages],
                    "max_tokens": 2048,
                }
                if tool_calling_supported:
                    request_args["tools"] = available_tools
                    request_args["tool_choice"] = "auto"

                try:
                    response = client.chat.completions.with_raw_response.create(
                        **request_args
                    )
                except Exception as error:
                    error_body = getattr(error, "body", None) or {}
                    error_message = str(error).lower()
                    if (
                        tool_calling_supported
                        and request.provider == "groq"
                        and (
                            "tool calling" in error_message
                            or "tool calling" in str(error_body).lower()
                        )
                    ):
                        tool_calling_supported = False
                        result = None
                        break
                    raise

                headers = response.headers
                completion = response.parse()

                save_provider_headers(request.provider, selected_model, headers)

                input_tokens = 0
                output_tokens = 0

                if completion.usage:
                    input_tokens = completion.usage.prompt_tokens or 0
                    output_tokens = completion.usage.completion_tokens or 0

                    turn_input_tokens += input_tokens
                    turn_output_tokens += output_tokens
                    has_turn_usage = True

                    record_usage(
                        request.provider,
                        selected_model,
                        tokens_used=input_tokens + output_tokens,
                    )

                record_provider_request(
                    request.provider,
                    selected_model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                )

                result = completion.choices[0].message
                tool_calls = result.tool_calls or []

                if not tool_calls:
                    if task_change_requested and claims_task_proposal(result.content):
                        proposal_tool_available = tool_calling_supported and any(
                            tool["function"]["name"] == "mcp_propose_google_task_change"
                            for tool in available_tools
                        )
                        if proposal_tool_available and not retry_task_proposal and round_number < max_tool_rounds:
                            retry_task_proposal = True
                            messages.append(result.model_dump(exclude_none=True))
                            messages.append({
                                "role": "system",
                                "content": (
                                    "No task proposal was saved. Use the actual "
                                    "mcp_propose_google_task_change tool to prepare the card. "
                                    "Use verified IDs from the lookup, or run the lookup first. "
                                    "If the target is ambiguous, ask the user to choose."
                                ),
                            })
                            continue
                        task_proposal = {"error": "No Google Task approval card was created."}
                    break

                if round_number >= max_tool_rounds:
                    messages.append(result.model_dump(exclude_none=True))

                    for call in tool_calls:
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call.id,
                            "content": "Tool-call limit reached. Answer using the information already retrieved.",
                        })

                    result = None
                    break

                if any(call.function.name not in enabled_names for call in tool_calls):
                    messages.append(result.model_dump(exclude_none=True))
                    for call in tool_calls:
                        messages.append({"role": "tool", "tool_call_id": call.id,
                                         "content": "Batch skipped. Use only the tools enabled for this request."})
                    continue

                calendar_actions = {
                    "mcp_update_calendar_event": "update",
                    "mcp_delete_calendar_event": "delete",
                }
                calendar_calls = [
                    call for call in tool_calls
                    if call.function.name in calendar_actions
                    or call.function.name == "mcp_create_calendar_events"
                ]

                gmail_calls = [
                    call for call in tool_calls
                    if call.function.name == "mcp_send_gmail_message"
                ]

                task_calls = [
                    call for call in tool_calls
                    if call.function.name == "mcp_propose_google_task_change"
                ]
                if task_calls:
                    if "mcp_propose_google_task_change" not in enabled_names:
                        task_proposal = {"error": "The Google Task proposal tool is disabled."}
                        break
                    if len(tool_calls) != 1:
                        messages.append(result.model_dump(exclude_none=True))
                        for call in tool_calls:
                            messages.append({
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": (
                                    "This batch was skipped. Run any needed lookups first. "
                                    "Then submit one resolved Google Task proposal by itself."
                                ),
                            })
                        continue

                    try:
                        arguments = json.loads(task_calls[0].function.arguments)
                        task_proposal = prepare_google_task_proposal(
                            arguments, request.conversation_id,
                        )
                    except Exception as error:
                        task_proposal = {"error": str(error)}
                    break

                if calendar_calls or gmail_calls:
                    approval_call_ids = {
                        call.id for call in calendar_calls + gmail_calls
                    }
                    ordinary_calls = [
                        call for call in tool_calls
                        if call.id not in approval_call_ids
                    ]

                    # Finish document/file creation before preparing an email
                    # that may attach one of those newly created files.
                    if gmail_calls and ordinary_calls:
                        messages.append(result.model_dump(exclude_none=True))

                        for call in tool_calls:
                            if call.id in approval_call_ids:
                                output = (
                                    "Finish the other requested tools first. "
                                    "Then call send_gmail_message again, using the "
                                    "created file's path relative to ~/Documents/My AI Agent "
                                    "in the attachments list. Do not prepare the email yet."
                                )
                            else:
                                try:
                                    arguments = json.loads(call.function.arguments)
                                    output = execute_chat_tool(call.function.name, arguments, message, calendar_reference_time)
                                except Exception as error:
                                    output = f"Tool error: {error}"

                            messages.append({
                                "role": "tool",
                                "tool_call_id": call.id,
                                "content": str(output),
                            })

                        continue

                    if calendar_calls:
                        print("[CALENDAR DEBUG] Calendar tool call intercepted.", flush=True)
                        try:
                            actions = []

                            for call in calendar_calls:
                                arguments = json.loads(call.function.arguments)

                                if call.function.name == "mcp_create_calendar_events":
                                    events = arguments.get("events")
                                    if not isinstance(events, list) or not events:
                                        raise ValueError("The calendar batch must contain at least one event.")

                                    for event in events:
                                        if not isinstance(event, dict):
                                            raise ValueError("Each calendar change must be an object.")
                                        event = dict(event)
                                        action = event.pop("action", "create")
                                        if action not in {"create", "update", "delete"}:
                                            raise ValueError(f"Unsupported calendar action: {action}")
                                        actions.append((event, action))
                                else:
                                    actions.append((
                                        arguments,
                                        calendar_actions[call.function.name],
                                    ))

                            # Load the original event before preparing an update.
                            # This keeps unchanged fields populated in the approval editor.
                            for arguments, action in actions:
                                if action == "delete":
                                    details = execute_tool("mcp_get_calendar_event_details", {"event_id": arguments.get("event_id")})
                                    if isinstance(details, str):
                                        details = json.loads(details)
                                    arguments["summary"] = details.get("summary") or "(Untitled event)"
                                if action != "update":
                                    continue

                                # Resolve "next weekday" once; apply it after
                                # loading the original event so omitted times are preserved.
                                weekdays = {
                                    "monday": 0, "tuesday": 1, "wednesday": 2,
                                    "thursday": 3, "friday": 4, "saturday": 5,
                                    "sunday": 6,
                                }
                                weekday_match = re.search(
                                    r"\bnext\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b",
                                    message.lower(),
                                )
                                target_date = None
                                if weekday_match:
                                    zone = ZoneInfo("America/Vancouver")
                                    timestamp_match = re.search(
                                        r"\((\d{4}-\d{2}-\d{2}T[^)]+)\)",
                                        str(calendar_reference_time),
                                    )
                                    if not timestamp_match:
                                        raise ValueError(
                                            "Authoritative calendar time is unavailable."
                                        )
                                    today = datetime.fromisoformat(
                                        timestamp_match.group(1)
                                    ).astimezone(zone).date()
                                    target_weekday = weekdays[weekday_match.group(1)]
                                    monday = today - timedelta(days=today.weekday())
                                    target_date = monday + timedelta(days=7 + target_weekday)

                                event_id = str(arguments.get("event_id", "")).strip()
                                details_raw = execute_tool(
                                    "mcp_get_calendar_event_details",
                                    {"event_id": event_id},
                                )
                                details = (
                                    json.loads(details_raw)
                                    if isinstance(details_raw, str)
                                    else details_raw
                                )
                                if not isinstance(details, dict):
                                    raise ValueError(
                                        "Could not retrieve the existing calendar event."
                                    )

                                for field in ("summary", "description", "location", "recurrence"):
                                    if not arguments.get(field):
                                        default = [] if field == "recurrence" else ""
                                        arguments[field] = details.get(field, default) or default

                                existing_start = details.get("start", {})
                                existing_end = details.get("end", {})

                                if existing_start.get("dateTime"):
                                    if not arguments.get("start_time"):
                                        arguments["start_time"] = existing_start["dateTime"]
                                    if not arguments.get("end_time"):
                                        arguments["end_time"] = existing_end.get("dateTime", "")
                                elif existing_start.get("date"):
                                    if not arguments.get("start_date"):
                                        arguments["start_date"] = existing_start["date"]
                                    if not arguments.get("end_date"):
                                        arguments["end_date"] = existing_end.get("date", "")

                                if target_date:
                                    if arguments.get("start_time") and arguments.get("end_time"):
                                        for field in ("start_time", "end_time"):
                                            parsed = datetime.fromisoformat(
                                                arguments[field].replace("Z", "+00:00")
                                            )
                                            # Calendar times are interpreted as
                                            # Vancouver wall-clock times. Ignore any
                                            # stale offset supplied by the model.
                                            parsed = parsed.replace(tzinfo=zone)
                                            arguments[field] = parsed.replace(
                                                year=target_date.year,
                                                month=target_date.month,
                                                day=target_date.day,
                                            ).isoformat()
                                    elif arguments.get("start_date") and arguments.get("end_date"):
                                        arguments["start_date"] = target_date.isoformat()
                                        arguments["end_date"] = (
                                            target_date + timedelta(days=1)
                                        ).isoformat()

                                # When an all-day event is changed to a timed event,
                                # remove its date-only fields. Google Calendar events
                                # cannot contain both date and dateTime for the same
                                # start/end values.
                                if arguments.get("start_time") or arguments.get("end_time"):
                                    arguments.pop("start_date", None)
                                    arguments.pop("end_date", None)

                            # Recover a clearly specified second event if the model
                            # omitted it from an otherwise valid split proposal.
                            split_requested = bool(re.search(
                                r"\b(split|separate)\b",
                                message,
                                re.IGNORECASE,
                            ))
                            if split_requested:
                                updates = [
                                    arguments for arguments, action in actions
                                    if action == "update"
                                ]
                                creates = [
                                    arguments for arguments, action in actions
                                    if action == "create"
                                ]
                                if len(updates) == 1 and not creates:
                                    second_range = re.search(
                                        r"\bsecond\s+one\s+is\s+from\s+"
                                        r"(\d{3,4})\s*[-–]\s*(\d{3,4})\b",
                                        message,
                                        re.IGNORECASE,
                                    )
                                    second_title = re.search(
                                        r"\band\s+([A-Za-z][A-Za-z0-9 &'-]*?"
                                        r"\b(?:retest|test|exam))\b",
                                        message,
                                        re.IGNORECASE,
                                    )
                                    original_end = updates[0].get("end_time", "")
                                    original_start = updates[0].get("start_time", "")
                                    if second_range and second_title and original_end and original_start:
                                        def parse_compact_time(value):
                                            value = value.strip()
                                            hour, minute = (
                                                (int(value[:-2]), int(value[-2:]))
                                                if len(value) == 4
                                                else (int(value[0]), int(value[1:]))
                                            )
                                            if hour > 23 or minute > 59:
                                                raise ValueError("Invalid time in the second event range.")
                                            return hour, minute

                                        start_hour, start_minute = parse_compact_time(second_range.group(1))
                                        end_hour, end_minute = parse_compact_time(second_range.group(2))
                                        original_end_dt = datetime.fromisoformat(
                                            original_end.replace("Z", "+00:00")
                                        )
                                        if (start_hour, start_minute) != (
                                            original_end_dt.hour,
                                            original_end_dt.minute,
                                        ):
                                            raise ValueError(
                                                "The second event's stated start time does not "
                                                "match the original event's end time."
                                            )
                                        if (end_hour, end_minute) <= (start_hour, start_minute):
                                            raise ValueError("The second event must end after it starts.")
                                        second_start = original_end_dt.replace(
                                            hour=start_hour, minute=start_minute, second=0, microsecond=0
                                        )
                                        second_end = original_end_dt.replace(
                                            hour=end_hour, minute=end_minute, second=0, microsecond=0
                                        )
                                        second_event = {
                                            "summary": second_title.group(1).strip(),
                                            "start_time": second_start.isoformat(),
                                            "end_time": second_end.isoformat(),
                                        }
                                        actions.append((second_event, "create"))

                            # Never present an incomplete split for approval.
                            split_requested = bool(re.search(
                                r"\b(split|separate)\b",
                                message,
                                re.IGNORECASE,
                            ))
                            if split_requested:
                                update_count = sum(
                                    action == "update" for _, action in actions
                                )
                                create_count = sum(
                                    action == "create" for _, action in actions
                                )
                                if update_count < 1 or create_count < 1:
                                    raise ValueError(
                                        "This split request requires at least one "
                                        "update to the original event and one new "
                                        "event. The proposal was not created because "
                                        "it did not include both actions."
                                    )

                            print(f"[CALENDAR DEBUG] Preparing {len(actions)} actions.", flush=True)
                            calendar_proposal = create_pending_calendar_actions(
                                actions,
                                request.conversation_id,
                            )
                            print(f"[CALENDAR DEBUG] Proposal prepared: {len(calendar_proposal.get('pending_ids', []))} pending items.", flush=True)
                        except Exception as error:
                            calendar_proposal = {"error": str(error)}

                    if gmail_calls:
                        try:
                            if len(gmail_calls) != 1:
                                raise ValueError("Please prepare one email at a time.")
                            arguments = json.loads(gmail_calls[0].function.arguments)
                            gmail_proposal = create_pending_gmail_message(
                                arguments,
                                request.conversation_id,
                            )
                        except Exception as error:
                            gmail_proposal = {"error": str(error)}

                    break

                messages.append(result.model_dump(exclude_none=True))

                for call in tool_calls:
                    try:
                        arguments = json.loads(call.function.arguments)
                        output = execute_chat_tool(call.function.name, arguments, message, calendar_reference_time)
                    except Exception as error:
                        output = f"Tool error: {error}"

                    messages.append({
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": str(output),
                    })

            if result is None and calendar_proposal is None and task_proposal is None:
                try:
                    response = client.chat.completions.with_raw_response.create(
                        model=selected_model,
                        messages=[system_message, *messages],
                        max_tokens=2048,
                    )

                    headers = response.headers
                    completion = response.parse()

                    save_provider_headers(request.provider, selected_model, headers)

                    input_tokens = 0
                    output_tokens = 0

                    if completion.usage:
                        input_tokens = completion.usage.prompt_tokens or 0
                        output_tokens = completion.usage.completion_tokens or 0

                        turn_input_tokens += input_tokens
                        turn_output_tokens += output_tokens
                        has_turn_usage = True

                        record_usage(
                            request.provider,
                            selected_model,
                            tokens_used=input_tokens + output_tokens,
                        )

                    record_provider_request(
                        request.provider,
                        selected_model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )

                    result = completion.choices[0].message
                except Exception as final_error:
                    # Preserve useful tool results if the provider refuses
                    # the final synthesis request after the tool-call limit.
                    retrieved = [
                        str(item.get("content", "")).strip()
                        for item in messages
                        if item.get("role") == "tool" and item.get("content")
                    ]
                    retrieved = retrieved[-5:]
                    fallback = (
                        "I retrieved information, but the model could not "
                        "complete its final summary. Here are the latest tool results:\n\n"
                        + "\n\n".join(retrieved)
                    )
                    if not retrieved:
                        fallback = (
                            "I reached the tool-call limit and could not "
                            "generate a final response. Please try a narrower request."
                        )
                    print(
                        f"Final synthesis failed [{request.provider}/{selected_model}]: "
                        f"{final_error}",
                        flush=True,
                    )
                    result = type("FallbackMessage", (), {"content": fallback})()

            if (
                task_change_requested and task_proposal is None
                and result is not None and claims_task_proposal(result.content)
                and not calendar_proposal and not gmail_proposal
            ):
                task_proposal = {"error": "No Google Task approval card was created."}

            proposal_parts = []

            if calendar_proposal:
                if calendar_proposal.get("error"):
                    proposal_parts.append(
                        "I couldn't prepare that calendar proposal: " + calendar_proposal["error"]
                    )
                else:
                    event_count = len(calendar_proposal.get("pending_ids", []))
                    proposal_parts.append(
                        f"I've prepared {event_count} Google Calendar change(s). "
                        "Review the details and approve or decline the batch below.\\n\\n"
                        "<!--CALENDAR_APPROVAL:"
                        + json.dumps(calendar_proposal, ensure_ascii=False)
                        + "-->"
                    )

            if task_proposal:
                if "error" in task_proposal:
                    proposal_parts.append(
                        "I couldn't prepare that Google Task change: " + task_proposal["error"]
                    )
                else:
                    payload = json.dumps(task_proposal, ensure_ascii=False)
                    payload = payload.replace("<", "\\u003c").replace(">", "\\u003e")
                    proposal_parts.append(
                        "Review the proposed Google Task change below.\n\n"
                        "<!--TASK_APPROVAL:" + payload + "-->"
                    )

            if gmail_proposal:
                if gmail_proposal.get("error"):
                    proposal_parts.append(
                        "I couldn't prepare that email: " + gmail_proposal["error"]
                    )
                else:
                    proposal_parts.append(
                        "I've prepared an email. Review the recipient, subject, and message, "
                        "then approve or decline sending it.\\n\\n"
                        "<!--GMAIL_APPROVAL:"
                        + json.dumps(gmail_proposal, ensure_ascii=False)
                        + "-->"
                    )

            full_reply = "\\n\\n".join(proposal_parts) if proposal_parts else (result.content or "")
            print(f"[CALENDAR DEBUG] Final reply ready ({len(full_reply)} characters).", flush=True)

            for index in range(0, len(full_reply), 40):
                yield full_reply[index:index + 40]
            print("[CALENDAR DEBUG] Reply stream completed.", flush=True)

            if has_turn_usage:
                record_turn_usage(
                    request.provider,
                    selected_model,
                    turn_input_tokens,
                    turn_output_tokens,
                )

            if full_reply:
                save_message(
                    request.conversation_id,
                    "assistant",
                    full_reply,
                )

        except Exception as error:
            response = getattr(error, "response", None)
            status_code = getattr(error, "status_code", None)

            details = [
                f"Exception: {type(error).__name__}: {error}",
                f"Status: {status_code}",
                f"Response body: {response.text if response is not None else None}",
                f"Error body: {getattr(error, 'body', None)}",
                traceback.format_exc(),
                "=" * 60,
            ]

            with open(
                Path(__file__).resolve().parent / "api_debug.log",
                "a",
                encoding="utf-8",
            ) as log:
                log.write("\n".join(details) + "\n")

            yield "\n\n[Error: " + format_provider_error(error) + "]"

    return StreamingResponse(
        generate(),
        media_type="text/plain",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
