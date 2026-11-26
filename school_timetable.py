"""Parse parenthesized weekly school timetables into calendar approval inputs."""
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo


DAY = r"(?:mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:rs?(?:day)?)?|fri(?:day)?)s?"
DAYS = rf"{DAY}(?:\s+(?:and|&)\s+{DAY})*"
CLOCK = r"\d{1,2}:?\d{2}"
SPAN = rf"(?P<start>{CLOCK})\s*[-–]\s*(?P<end>{CLOCK})"
DAY_CODES = {"mo": "MO", "tu": "TU", "we": "WE", "th": "TH", "fr": "FR"}


def school_time(raw):
    digits = raw.replace(":", "")
    hour, minute = int(digits[:-2]), int(digits[-2:])
    if 1 <= hour <= 5:
        hour += 12
    if not 6 <= hour <= 18 or minute > 59:
        raise ValueError(f"Please give the school time {raw} in 24-hour format.")
    return time(hour, minute)


def weekly_school_actions(message, now):
    """Return None when the entire request does not fit the timetable grammar."""
    prefix = re.match(
        r"(?:put|add|create|schedule)\s+(?:these\s+)?(?:events|classes)"
        r"(?:\s+(?:in(?:to)?|on|to)\s+my\s+calendar)?\s*:\s*", message, re.I
    )
    if not prefix:
        return None
    body = message[prefix.end():].strip().rstrip(".")
    if not re.search(r"\b(?:school|class|choir|grade|[A-Za-z]+\s+(?:[1-9]|1[0-2]))\b", body, re.I):
        return None

    slots = []
    pos = 0
    while pos < len(body):
        group = re.match(r"\s*([^(),;:\n]+?)\s*\(([^()]+)\)[\s,;]*", body[pos:])
        if not group:
            return None
        pos += group.end()
        title, schedule = group.groups()
        repeating = re.match(r"\s*repeat\s*:\s*", schedule, re.I)
        if repeating:
            schedule = schedule[repeating.end():]
        for slot in schedule.split(","):
            slot = slot.strip()
            match = (re.fullmatch(rf"{SPAN}\s+({DAYS})", slot, re.I)
                     or re.fullmatch(rf"({DAYS})\s+{SPAN}", slot, re.I))
            if not match:
                return None
            days = re.findall(rf"\b{DAY}\b", slot.lower())
            if not repeating and not all(day.endswith("days") for day in days):
                return None
            slots.append((title.strip(), days, match["start"], match["end"]))

    local_now = now.astimezone(ZoneInfo("America/Vancouver"))
    actions = []
    for title, days, raw_start, raw_end in slots:
        start, end = school_time(raw_start), school_time(raw_end)
        if end <= start:
            raise ValueError(f"Please clarify {title}'s time range {raw_start}–{raw_end}.")
        codes = [DAY_CODES[day[:2]] for day in days]
        starts = []
        for code in codes:
            weekday = list(DAY_CODES.values()).index(code)
            first_date = local_now.date() + timedelta(days=(weekday - local_now.weekday()) % 7)
            first = datetime.combine(first_date, start, local_now.tzinfo)
            starts.append(first if first >= local_now else first + timedelta(weeks=1))
        first = min(starts)
        actions.append(({
            "summary": title,
            "start_time": first.isoformat(),
            "end_time": datetime.combine(first.date(), end, local_now.tzinfo).isoformat(),
            "recurrence": ["RRULE:FREQ=WEEKLY;BYDAY=" + ",".join(codes)],
        }, "create"))
    return actions or None
