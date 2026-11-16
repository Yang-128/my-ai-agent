import sqlite3
import json
import uuid
import re
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from runtime import DB_PATH

GROQ_REFILL_INTERVAL_SECONDS = 90

# Official Groq Free Tier daily token limits.
GROQ_TPD_LIMITS = {
    "openai/gpt-oss-120b": 200000,
}

# Limits from the Gemini AI Studio Free Tier table you provided.
# Unknown models deliberately have no assumed limits.
GEMINI_LIMITS = {
    # Text generation models
    "gemini-3.8-flash": {"rpm": 5, "tpm": 250000, "rpd": 20},
    "gemini-3.5-flash-lite": {"rpm": 15, "tpm": 250000, "rpd": 500},
    "gemini-2.5-flash": {"rpm": 5, "tpm": 250000, "rpd": 20},
    "gemini-2.5-flash-lite": {"rpm": 10, "tpm": 250000, "rpd": 20},
    "gemini-3-flash": {"rpm": 5, "tpm": 250000, "rpd": 20},
    "gemini-3.1-flash-lite": {"rpm": 15, "tpm": 250000, "rpd": 500},
    "gemini-3.6-flash": {"rpm": 5, "tpm": 250000, "rpd": 20},
    "gemini-4-hydrogen": {"rpm": 5, "tpm": 250000, "rpd": 20},

    # Gemma models
    "gemma-4-26b": {"rpm": 30, "tpm": 16000, "rpd": 14400},
    "gemma-4-26b-a4b-it": {"rpm": 30, "tpm": 16000, "rpd": 14400},
    "gemma-4-31b": {"rpm": 30, "tpm": 16000, "rpd": 14400},
    "gemma-4-31b-it": {"rpm": 30, "tpm": 16000, "rpd": 14400},

    # Specialized models
    "gemini-2.5-flash-tts": {"rpm": 3, "tpm": 10000, "rpd": 10},
    "gemini-3.1-flash-tts": {"rpm": 3, "tpm": 10000, "rpd": 10},
    "gemini-3.5-transcribe": {"rpm": 3, "tpm": 10000, "rpd": 25},
    "gemini-3.8-flash-lite-tts": {"rpm": 3, "tpm": 10000, "rpd": 10},
    "gemini-3.8-flash-tts": {"rpm": 3, "tpm": 10000, "rpd": 10},

    # Other models
    "gemini-decision-1": {"rpm": 200, "tpm": 50000, "rpd": 10000},
    "gemini-embedding-1": {"rpm": 100, "tpm": 30000, "rpd": 1000},
    "gemini-embedding-2": {"rpm": 100, "tpm": 30000, "rpd": 1000},
}


def connect():
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    return db


def now():
    return datetime.now(timezone.utc).isoformat()


def usage_day(provider, current_time=None):
    current_time = current_time or datetime.now(timezone.utc)
    if provider == "gemini":
        return current_time.astimezone(
            ZoneInfo("America/Los_Angeles")
        ).date().isoformat()
    return current_time.date().isoformat()


def get_gemini_limits(model):
    model = (model or "").lower().removeprefix("models/")
    return GEMINI_LIMITS.get(model)


def get_default_gemini_rpd(model):
    limits = get_gemini_limits(model)
    return limits["rpd"] if limits else None


def init_db():
    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (conversation_id)
                    REFERENCES conversations(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS usage_daily (
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                day_utc TEXT NOT NULL,
                requests_limit INTEGER,
                requests_remaining INTEGER,
                requests_reset TEXT,
                tokens_limit INTEGER,
                tokens_used INTEGER DEFAULT 0,
                updated_at TEXT NOT NULL,
                next_replenishment_at TEXT,
                PRIMARY KEY (provider, model, day_utc)
            );

            CREATE TABLE IF NOT EXISTS usage_turns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                day_utc TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS usage_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                input_tokens INTEGER DEFAULT 0,
                output_tokens INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_usage_events_created
            ON usage_events(created_at);

            CREATE INDEX IF NOT EXISTS idx_usage_events_provider_model
            ON usage_events(provider, model, created_at);

            CREATE TABLE IF NOT EXISTS tool_settings (
                tool_name TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            );
        """)

        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(messages)").fetchall()
        }
        if "images" not in columns:
            db.execute(
                "ALTER TABLE messages ADD COLUMN images TEXT NOT NULL DEFAULT '[]'"
            )


def create_conversation():
    conversation_id = str(uuid.uuid4())
    timestamp = now()

    with connect() as db:
        db.execute(
            """INSERT INTO conversations
               (id, title, created_at, updated_at)
               VALUES (?, ?, ?, ?)""",
            (conversation_id, "New conversation", timestamp, timestamp)
        )

    return conversation_id


def list_conversations():
    with connect() as db:
        rows = db.execute(
            """SELECT id, title, created_at, updated_at
               FROM conversations ORDER BY updated_at DESC"""
        ).fetchall()

    return [dict(row) for row in rows]


def get_conversation(conversation_id):
    with connect() as db:
        conversation = db.execute(
            "SELECT * FROM conversations WHERE id = ?",
            (conversation_id,)
        ).fetchone()

        if not conversation:
            return None

        messages = db.execute(
            """SELECT role, content, images FROM messages
               WHERE conversation_id = ? ORDER BY id""",
            (conversation_id,)
        ).fetchall()

    result = dict(conversation)
    result["messages"] = []
    for row in messages:
        item = dict(row)
        try:
            item["images"] = json.loads(item.get("images") or "[]")
        except (TypeError, ValueError):
            item["images"] = []
        result["messages"].append(item)
    return result


def save_message(conversation_id, role, content, images=None):
    timestamp = now()

    with connect() as db:
        db.execute(
            """INSERT INTO messages
               (conversation_id, role, content, images, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                conversation_id,
                role,
                content,
                json.dumps(images or [], ensure_ascii=False),
                timestamp,
            )
        )

        if role == "user":
            conversation = db.execute(
                "SELECT title FROM conversations WHERE id = ?",
                (conversation_id,)
            ).fetchone()

            if conversation and conversation["title"] == "New conversation":
                title = content.strip().replace("\n", " ")[:45]
                db.execute(
                    "UPDATE conversations SET title = ? WHERE id = ?",
                    (title or "New conversation", conversation_id)
                )

        db.execute(
            "UPDATE conversations SET updated_at = ? WHERE id = ?",
            (timestamp, conversation_id)
        )


def parse_reset_seconds(value):
    if not value:
        return GROQ_REFILL_INTERVAL_SECONDS

    matches = re.findall(
        r"(\d+(?:\.\d+)?)\s*(h|m|s)",
        str(value).lower()
    )

    if not matches:
        return GROQ_REFILL_INTERVAL_SECONDS

    multipliers = {"h": 3600, "m": 60, "s": 1}

    return sum(
        float(number) * multipliers[unit]
        for number, unit in matches
    )


def record_usage(
    provider,
    model,
    requests_limit=None,
    requests_remaining=None,
    requests_reset=None,
    tokens_limit=None,
    tokens_used=0
):
    timestamp = now()
    day = usage_day(provider)
    next_replenishment = None

    if provider == "groq" and requests_remaining is not None:
        seconds = parse_reset_seconds(requests_reset)
        next_replenishment = (
            datetime.now(timezone.utc)
            + timedelta(seconds=seconds)
        ).isoformat()

    with connect() as db:
        db.execute("""
            INSERT INTO usage_daily (
                provider, model, day_utc,
                requests_limit, requests_remaining,
                requests_reset, tokens_limit,
                tokens_used, updated_at,
                next_replenishment_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, model, day_utc)
            DO UPDATE SET
                requests_limit = COALESCE(
                    excluded.requests_limit,
                    usage_daily.requests_limit
                ),
                requests_remaining = COALESCE(
                    excluded.requests_remaining,
                    usage_daily.requests_remaining
                ),
                requests_reset = COALESCE(
                    excluded.requests_reset,
                    usage_daily.requests_reset
                ),
                tokens_limit = COALESCE(
                    excluded.tokens_limit,
                    usage_daily.tokens_limit
                ),
                tokens_used = usage_daily.tokens_used + excluded.tokens_used,
                updated_at = excluded.updated_at,
                next_replenishment_at = COALESCE(
                    excluded.next_replenishment_at,
                    usage_daily.next_replenishment_at
                )
        """, (
            provider, model, day,
            requests_limit, requests_remaining,
            requests_reset, tokens_limit,
            tokens_used, timestamp,
            next_replenishment
        ))


def record_provider_request(
    provider,
    model,
    requests_limit=None,
    input_tokens=0,
    output_tokens=0
):
    """Record one actual provider API request for rolling RPM/TPM tracking."""
    timestamp = now()
    current_time = datetime.now(timezone.utc)
    day = usage_day(provider, current_time)

    with connect() as db:
        db.execute("""
            INSERT INTO usage_events (
                provider, model, input_tokens,
                output_tokens, created_at
            )
            VALUES (?, ?, ?, ?, ?)
        """, (
            provider, model,
            input_tokens or 0,
            output_tokens or 0,
            timestamp
        ))

        # Gemini quotas are project/model-specific and locally tracked.
        if provider == "gemini":
            if requests_limit is None:
                requests_limit = get_default_gemini_rpd(model)

            if requests_limit is not None:
                existing = db.execute("""
                    SELECT requests_remaining
                    FROM usage_daily
                    WHERE provider = ? AND model = ? AND day_utc = ?
                """, (provider, model, day)).fetchone()

                if existing and existing["requests_remaining"] is not None:
                    remaining = max(0, existing["requests_remaining"] - 1)
                else:
                    remaining = max(0, requests_limit - 1)

                db.execute("""
                    INSERT INTO usage_daily (
                        provider, model, day_utc,
                        requests_limit, requests_remaining,
                        updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(provider, model, day_utc)
                    DO UPDATE SET
                        requests_limit = excluded.requests_limit,
                        requests_remaining = excluded.requests_remaining,
                        updated_at = excluded.updated_at
                """, (
                    provider, model, day,
                    requests_limit, remaining,
                    timestamp
                ))



def get_daily_usage():
    current_time = datetime.now(timezone.utc)
    since = (current_time - timedelta(seconds=60)).isoformat()

    # Daily usage boundaries:
    # Gemini: midnight America/Los_Angeles
    # OpenRouter and Groq: midnight UTC for local tracking.
    pacific_now = current_time.astimezone(
        ZoneInfo("America/Los_Angeles")
    )
    gemini_day_start = (
        pacific_now.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    ).astimezone(timezone.utc)

    utc_day_start = current_time.replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    token_window_start = min(gemini_day_start, utc_day_start)

    with connect() as db:
        rows = db.execute("""
            SELECT u.*
            FROM usage_daily AS u
            WHERE u.day_utc = CASE
                WHEN u.provider = 'gemini' THEN ?
                ELSE ?
            END
            ORDER BY u.provider, u.model
        """, (
            pacific_now.date().isoformat(),
            utc_day_start.date().isoformat(),
        )).fetchall()

        event_rows = db.execute("""
            SELECT
                provider,
                model,
                COUNT(*) AS rpm_used,
                SUM(input_tokens) AS input_tokens,
                SUM(output_tokens) AS output_tokens
            FROM usage_events
            WHERE created_at >= ?
            GROUP BY provider, model
        """, (since,)).fetchall()

        daily_event_rows = db.execute("""
            SELECT provider, model, COUNT(*) AS rpd_events
            FROM usage_events
            WHERE created_at >= ?
            GROUP BY provider, model
        """, (utc_day_start.isoformat(),)).fetchall()

        token_event_rows = db.execute("""
            SELECT
                provider,
                model,
                input_tokens,
                output_tokens,
                created_at
            FROM usage_events
            WHERE created_at >= ?
        """, (token_window_start.isoformat(),)).fetchall()

    recent = {
        (row["provider"], row["model"]): dict(row)
        for row in event_rows
    }

    daily_events = {
        (row["provider"], row["model"]): row["rpd_events"]
        for row in daily_event_rows
    }

    daily_token_usage = {}

    for event in token_event_rows:
        provider = event["provider"]
        model = event["model"]
        key = (provider, model)

        tokens = (
            (event["input_tokens"] or 0)
            + (event["output_tokens"] or 0)
        )

        event_time = datetime.fromisoformat(event["created_at"])

        day_start = (
            gemini_day_start
            if provider == "gemini"
            else utc_day_start
        )

        if event_time >= day_start:
            daily_token_usage[key] = (
                daily_token_usage.get(key, 0) + tokens
            )

    results = []

    for row in rows:
        item = dict(row)
        provider = item["provider"]
        model = item["model"]
        key = (provider, model)

        event = recent.get(key, {})
        rpm_used = event.get("rpm_used", 0) or 0
        input_tokens = event.get("input_tokens", 0) or 0
        output_tokens = event.get("output_tokens", 0) or 0

        tpm_used = (
            input_tokens
            if provider == "gemini"
            else input_tokens + output_tokens
        )

        if provider == "openrouter" and model.endswith(":free"):
            item["rpd_used"] = daily_events.get(key, 0)
        else:
            item["rpd_used"] = (
                max(0, item["requests_limit"] - item["requests_remaining"])
                if item["requests_limit"] is not None
                and item["requests_remaining"] is not None
                else None
            )

        item["rpm_used"] = rpm_used
        item["tpm_used"] = tpm_used
        item["tpd_used"] = daily_token_usage.get(key, 0)

        # No verified official TPD/TPW limits are configured.
        item["tpd_limit"] = (
            GROQ_TPD_LIMITS.get(model)
            if provider == "groq"
            else None
        )

        gemini_limits = (
            get_gemini_limits(model) if provider == "gemini" else None
        )

        if gemini_limits:
            item["rpm_limit"] = gemini_limits["rpm"]
            item["tpm_limit"] = gemini_limits["tpm"]
            item["rpd_limit"] = gemini_limits["rpd"]
            item["limit_source"] = "Gemini AI Studio Free Tier"
        else:
            item["rpm_limit"] = (
                30 if provider == "groq" and model == "openai/gpt-oss-120b"
                else 20 if provider == "openrouter" and model.endswith(":free")
                else None
            )
            item["tpm_limit"] = item["tokens_limit"]
            item["rpd_limit"] = item["requests_limit"]
            item["limit_source"] = (
                "Provider API" if (
                    item["requests_limit"] is not None
                    or item["tokens_limit"] is not None
                )
                else "Local tracking"
            )

        if (
            provider == "groq"
            and item["requests_remaining"] is not None
            and item["requests_limit"] is not None
        ):
            remaining = item["requests_remaining"]
            next_time = item["next_replenishment_at"]

            if next_time:
                next_time = datetime.fromisoformat(next_time)

                if current_time >= next_time:
                    intervals = int(
                        (current_time - next_time).total_seconds()
                        // GROQ_REFILL_INTERVAL_SECONDS
                    ) + 1

                    remaining = min(
                        item["requests_limit"],
                        remaining + intervals
                    )

                    next_time += timedelta(
                        seconds=intervals * GROQ_REFILL_INTERVAL_SECONDS
                    )

                if remaining >= item["requests_limit"]:
                    next_time = None

                item["next_replenishment_at"] = (
                    next_time.isoformat() if next_time else None
                )

            item["requests_remaining"] = remaining
            item["rpd_used"] = max(
                0, item["requests_limit"] - remaining
            )

        reset_at = None

        if provider == "gemini":
            next_midnight = (
                pacific_now.replace(
                    hour=0, minute=0, second=0, microsecond=0
                ) + timedelta(days=1)
            )
            reset_at = next_midnight.astimezone(timezone.utc)

        elif provider == "openrouter" and model.endswith(":free"):
            reset_at = utc_day_start + timedelta(days=1)

        if reset_at:
            seconds = max(
                0, int((reset_at - current_time).total_seconds())
            )
            hours, remainder = divmod(seconds, 3600)
            minutes, secs = divmod(remainder, 60)

            item["requests_reset"] = f"{hours}h{minutes}m{secs}s"
            item["updated_at"] = current_time.isoformat()

        results.append(item)

    return results


def record_turn_usage(provider, model, input_tokens, output_tokens):
    timestamp = now()
    day = usage_day(provider)

    with connect() as db:
        db.execute("""
            INSERT INTO usage_turns (
                provider, model, day_utc, input_tokens,
                output_tokens, total_tokens, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            provider, model, day, input_tokens,
            output_tokens, input_tokens + output_tokens, timestamp
        ))


def get_turn_usage():
    current_time = datetime.now(timezone.utc)

    pacific_now = current_time.astimezone(
        ZoneInfo("America/Los_Angeles")
    )
    gemini_day_start = pacific_now.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc).isoformat()

    utc_day_start = current_time.replace(
        hour=0, minute=0, second=0, microsecond=0
    ).isoformat()

    with connect() as db:
        rows = db.execute("""
            WITH event_starts AS (
                SELECT
                    provider,
                    model,
                    MIN(created_at) AS first_event
                FROM usage_events
                WHERE created_at >= CASE
                    WHEN provider = 'gemini' THEN ?
                    ELSE ?
                END
                GROUP BY provider, model
            )
            SELECT
                t.provider,
                t.model,
                COUNT(*) AS turns,
                SUM(t.input_tokens) AS input_tokens,
                SUM(t.output_tokens) AS output_tokens,
                SUM(t.total_tokens) AS total_tokens,
                ROUND(AVG(t.total_tokens)) AS average_tokens_per_turn
            FROM usage_turns AS t
            JOIN event_starts AS e
                ON e.provider = t.provider
                AND e.model = t.model
            WHERE t.created_at >= e.first_event
            GROUP BY t.provider, t.model
            ORDER BY t.provider, t.model
        """, (gemini_day_start, utc_day_start)).fetchall()

    return [dict(row) for row in rows]



def get_tool_settings():
    with connect() as db:
        rows = db.execute(
            "SELECT tool_name, enabled FROM tool_settings"
        ).fetchall()

    return {row["tool_name"]: bool(row["enabled"]) for row in rows}


def set_tool_enabled(tool_name, enabled):
    with connect() as db:
        db.execute("""
            INSERT INTO tool_settings (tool_name, enabled, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(tool_name) DO UPDATE SET
                enabled = excluded.enabled,
                updated_at = excluded.updated_at
        """, (tool_name, int(bool(enabled)), now()))

    return bool(enabled)
