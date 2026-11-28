import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import storage


class ConversationTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = patch.object(storage, "DB_PATH", Path(directory.name) / "test.db")
        database.start()
        self.addCleanup(database.stop)
        storage.init_db()

    def test_saved_conversation_retains_title_and_message_order(self):
        conversation = storage.create_conversation()
        storage.save_message(conversation, "user", "First question")
        storage.save_message(conversation, "assistant", "First answer")
        storage.save_message(conversation, "user", "Follow-up")

        result = storage.get_conversation(conversation)
        self.assertEqual(result["title"], "First question")
        self.assertEqual(
            [(m["role"], m["content"]) for m in result["messages"]],
            [("user", "First question"), ("assistant", "First answer"), ("user", "Follow-up")],
        )
        self.assertEqual([c["id"] for c in storage.list_conversations()], [conversation])

    def test_full_titles_and_legacy_title_recovery(self):
        text = "A long opening question " * 8 + "\n最後の質問"
        title = text.replace("\n", " ")
        legacy = storage.create_conversation()
        custom = storage.create_conversation()
        for cid in (legacy, custom):
            storage.save_message(cid, "user", text)
            self.assertEqual(storage.get_conversation(cid)["title"], title)
        with storage.connect() as db:
            db.execute("UPDATE conversations SET title = ? WHERE id = ?", (title[:45], legacy))
            db.execute("UPDATE conversations SET title = ? WHERE id = ?", ("x" * 45, custom))
            db.execute("PRAGMA user_version = 0")
        storage.init_db()
        storage.init_db()
        self.assertEqual(storage.get_conversation(legacy)["title"], title)
        self.assertEqual(storage.get_conversation(custom)["title"], "x" * 45)

    def test_unknown_conversation(self):
        self.assertIsNone(storage.get_conversation("missing"))

    def test_usage_distinguishes_reference_api_and_unknown_limits(self):
        storage.record_provider_request("gemini", "gemini-3.5-flash-lite", input_tokens=10)
        storage.record_usage("gemini", "unlisted-model", tokens_used=10)
        storage.record_usage("groq", "openai/gpt-oss-120b", requests_limit=1000, tokens_limit=8000)
        rows = {item["model"]: item for item in storage.get_daily_usage()}
        self.assertEqual(rows["gemini-3.5-flash-lite"]["quota_sources"]["rpd"], "Free-plan reference")
        self.assertEqual(rows["unlisted-model"]["quota_sources"]["rpd"], "Limit unknown")
        groq = rows["openai/gpt-oss-120b"]["quota_sources"]
        self.assertEqual(groq["rpd"], "Provider API")
        self.assertEqual(groq["tpd"], "Free-plan reference")
