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

    def test_unknown_conversation(self):
        self.assertIsNone(storage.get_conversation("missing"))
