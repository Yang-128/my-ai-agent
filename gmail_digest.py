"""Conservative coverage protection for daily Gmail digests."""

import html
import json
import re


ACTION_PATTERN = re.compile(
    r"\b(?:volunteer(?:ing)?|shifts?|scholarships?|"
    r"admissions?|assignments?|exams?|interviews?|"
    r"appointments?|availability|deadlines?|"
    r"action required|response required|"
    r"please confirm|please respond|"
    r"please submit|schedule change|"
    r"payment due|invoices?|receipts?|"
    r"security alerts?|verification|"
    r"booking confirmation|rsvp)\b",
    re.IGNORECASE,
)


def is_daily_digest(message):
    text = " ".join(message.lower().strip().split())
    text = text.rstrip("?!.")

    patterns = (
        r"(?:do i have|have i received|are there|any)"
        r"(?: any)? (?:new |recent )?(?:emails?|mail)(?: today)?",

        r"(?:check|show|summarize|summarise)(?: me)?"
        r"(?: my)? (?:new |recent )?(?:emails?|mail)(?: today)?",

        r"what (?:emails?|mail) (?:did i receive|came in)(?: today)?",

        r"(?:today's email digest|daily email digest|email digest(?: for today)?)",
    )

    return any(re.fullmatch(pattern, text) for pattern in patterns)


def priority_message(message):
    # Evaluate subject and snippet before considering marketing labels.
    # Unread status is deliberately not a priority signal.
    text = " ".join([
        str(message.get("subject", "")),
        str(message.get("snippet", "")),
    ])

    return bool(ACTION_PATTERN.search(text)) or bool(message.get("starred"))


def collect_daily_digest(fetch, max_pages=4):
    messages = []
    seen = set()
    page_token = ""
    seen_tokens = set()
    complete = False

    for _ in range(max_pages):
        try:
            raw = fetch({
                "today": True,
                "max_results": 25,
                "page_token": page_token,
            })
            page = json.loads(raw) if isinstance(raw, str) else raw

            for message in page["messages"]:
                message_id = message.get("id")

                if not message_id or message_id in seen:
                    continue

                seen.add(message_id)

                item = {
                    key: message.get(key)
                    for key in (
                        "id", "thread_id", "sender", "subject",
                        "date", "unread", "important", "starred",
                        "categories",
                    )
                }

                item["snippet"] = str(message.get("snippet", ""))[:350]
                item["priority_candidate"] = priority_message(message)
                messages.append(item)

            page_token = page.get("next_page_token")

            if not page_token:
                complete = True
                break

            if page_token in seen_tokens:
                break

            seen_tokens.add(page_token)

        except Exception:
            break

    messages.sort(
        key=lambda item: not item["priority_candidate"]
    )

    return {
        "messages": messages,
        "reviewed_count": len(messages),
        "complete": complete,
    }


def safe_text(value):
    value = " ".join(str(value or "").split())
    value = html.escape(value)
    return re.sub(r"([\\`*_{}\[\]])", r"\\\1", value)


def normalize(value):
    return " ".join(str(value or "").casefold().split())


def ensure_digest_coverage(reply, digest):
    reply = reply or ""
    normalized = normalize(reply)
    missing = []

    for message in digest["messages"]:
        if not message["priority_candidate"]:
            continue

        subject = message.get("subject") or "(No subject)"

        if normalize(subject) not in normalized:
            missing.append(message)

    if missing:
        reply += "\n\n### Additional emails worth reviewing\n"

        for message in missing:
            reply += (
                "\n- **" + safe_text(message.get("subject") or "(No subject)")
                + "** — " + safe_text(message.get("sender"))
            )

            if message.get("snippet"):
                reply += "\n  " + safe_text(message["snippet"])

    if not digest["complete"]:
        reply += (
            "\n\nInbox review was incomplete. "
            f"{digest['reviewed_count']} messages were retrieved; "
            "additional messages may require review."
        )

    return reply.strip()
