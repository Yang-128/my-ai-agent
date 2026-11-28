"""Token-efficient tool loading with conservative automatic fallback."""

import re


EXPAND_TOOL_NAME = "request_tool_groups"

GROUPS = {
    "email": "Gmail search, message reading, threads and sending",
    "calendar": "Calendar lookup and change proposals",
    "tasks": "Google Tasks lookup and change proposals",
    "web": "Internet search and webpage retrieval",
    "files": "Local file discovery and reading",
    "documents": "Document creation",
    "memory": "Previous conversation retrieval",
    "system": "Current time and system information",
    "calculations": "Decimal arithmetic, percentages and unit conversions",
    "other": "Other registered capabilities",
}

EXPAND_TOOL = {
    "type": "function",
    "function": {
        "name": EXPAND_TOOL_NAME,
        "description": (
            "Load additional capabilities needed to complete the user's "
            "request. Call this before attempting an unavailable tool. "
            "Loading capabilities does not authorize external changes."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "groups": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "enum": [*GROUPS, "all"],
                    },
                    "description": "; ".join(
                        f"{key}: {value}"
                        for key, value in GROUPS.items()
                    ),
                }
            },
            "required": ["groups"],
        },
    },
}


def tool_name(tool):
    return tool["function"]["name"]


def tool_group(name):
    name = name.removeprefix("mcp_")

    if name in {"calculate_decimal", "summarize_decimal_rows"}:
        return "calculations"
    if "gmail" in name:
        return "email"
    if "calendar" in name:
        return "calendar"
    if "google_task" in name:
        return "tasks"
    if name.startswith("web_"):
        return "web"
    if "create_document" in name:
        return "documents"
    if "conversation" in name:
        return "memory"
    if "file" in name:
        return "files"
    if "system_info" in name or "current_time" in name:
        return "system"

    return "other"


PATTERNS = {
    "email": (
        r"\b(?:emails?|gmail|inbox|unread mail|"
        r"mailbox|email thread)\b"
    ),
    "calendar": (
        r"\b(?:calendar|schedule|events?|appointments?|"
        r"meetings?|rehearsals?|reschedule)\b"
    ),
    "tasks": (
        r"\b(?:google tasks|tasks?|to-do|todo|"
        r"checklist|reminders?)\b"
    ),
    "web": (
        r"\b(?:web|online|internet|browse|"
        r"look up|latest|up-to-date|current news|"
        r"search for sources)\b|https?://"
    ),
    "files": (
        r"\b(?:files?|folders?|directories|"
        r"on my mac|local documents?|"
        r"on my computer)\b"
    ),
    "documents": (
        r"\b(?:pdf|docx|xlsx|pptx|spreadsheet|"
        r"word document|powerpoint|"
        r"create a document|generate a document)\b"
    ),
    "memory": (
        r"\b(?:previous conversation|past conversations?|"
        r"chat history|earlier conversation|"
        r"remember when|we discussed)\b"
    ),
    "system": (
        r"\b(?:current time|what time is it|"
        r"what day is it|current date|"
        r"system information|disk space|"
        r"battery status)\b"
    ),
}


GENERAL_START = re.compile(
    r"^(?:please\s+)?(?:explain|define|"
    r"teach me|solve|calculate|simplify|"
    r"derive|prove|translate|"
    r"write (?:a |an )?(?:python |java |javascript )?"
    r"(?:function|program|code))\b",
    re.I,
)

EXTERNAL_HINT = re.compile(
    r"\b(?:my|our|today|tomorrow|yesterday|"
    r"currently|current|recent|latest|"
    r"right now|this week|last week|"
    r"attached|previous|earlier|"
    r"that one|this one|verify|research|"
    r"find|check|retrieve|download|"
    r"send|update|delete|remember)\b",
    re.I,
)


def scoped_tools(selected, registry):
    selected_names = {
        tool_name(tool)
        for tool in selected
        if tool_name(tool) != EXPAND_TOOL_NAME
    }

    result = [
        tool for tool in registry
        if tool_name(tool) in selected_names
    ]

    if len(result) < len(registry):
        result.append(EXPAND_TOOL)

    return result


def select_initial_tools(
    message, registry, *, has_images=False, daily_digest=False
):
    registry = list(registry)
    text = " ".join(message.lower().split())

    if not registry:
        return [], "full"

    if daily_digest:
        return [
            tool for tool in registry
            if tool_group(tool_name(tool)) == "email"
            and tool_name(tool) != "mcp_send_gmail_message"
        ], "digest"

    # Image-dependent intent may not be apparent from text.
    if has_images:
        return registry, "full"

    groups = {
        group for group, pattern in PATTERNS.items()
        if re.search(pattern, text, re.I)
    }

    if "mcp_calculate_decimal" in {tool_name(tool) for tool in registry} and re.search(
        r"\b(?:calculate|percentage|unit conversion|sum of|average of|group(?:ed)? totals|group by|convert [+-]?\d)\b", text, re.I
    ):
        groups.add("calculations")

    # General questions receive no tool definitions only when
    # there are no obvious external-data requirements.
    if not groups:
        if GENERAL_START.search(text) and not EXTERNAL_HINT.search(text):
            return [], "general"

        # Short follow-ups and ambiguous requests get full access.
        return registry, "full"

    if "calendar" in groups and re.search(
        r"\b(?:schedule|agenda|availability)\b", text
    ):
        groups.add("tasks")

    selected = [
        tool for tool in registry
        if tool_group(tool_name(tool)) in groups
    ]

    # Reading email should not load the sending schema by default.
    if "email" in groups and not re.search(
        r"\b(?:send|reply|forward|draft|compose|prepare)\b",
        text,
    ):
        selected = [
            tool for tool in selected
            if tool_name(tool) != "mcp_send_gmail_message"
        ]

    if not selected:
        return registry, "full"

    if len(selected) == len(registry):
        return registry, "full"

    return scoped_tools(selected, registry), "scoped"


def expand_tool_groups(active, registry, groups):
    if not isinstance(groups, list):
        raise ValueError("groups must be a list")

    if not groups or any(
        not isinstance(group, str)
        or group not in {*GROUPS, "all"}
        for group in groups
    ):
        raise ValueError("Unknown tool group")

    if "all" in groups:
        return list(registry)

    names = {
        tool_name(tool) for tool in active
        if tool_name(tool) != EXPAND_TOOL_NAME
    }

    names.update(
        tool_name(tool)
        for tool in registry
        if tool_group(tool_name(tool)) in groups
    )

    selected = [
        tool for tool in registry
        if tool_name(tool) in names
    ]

    return scoped_tools(selected, registry)
