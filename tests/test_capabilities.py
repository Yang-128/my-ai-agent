import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import app
import storage
from providers import free_chat_models


class CapabilityTests(unittest.TestCase):
    def test_only_free_text_chat_models_are_offered(self):
        model = {"id": "example:free", "pricing": {"prompt": "0", "completion": "0"},
                 "architecture": {"input_modalities": ["text", "image"], "output_modalities": ["text"]},
                 "context_length": 32000, "supported_parameters": ["tools"]}
        results = free_chat_models([model, {**model, "pricing": {"prompt": "0"}},
                                    {**model, "pricing": {**model["pricing"], "request": "1"}},
                                    {**model, "architecture": {"output_modalities": ["image"]}}])
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0]["capabilities"]["tools"])
        self.assertTrue(results[0]["capabilities"]["image"])
        self.assertEqual(results[0]["context_length"], 32000)

    def test_document_navigation_preserves_dotted_folders(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(app, "DOCUMENTS_ROOT", Path(folder)):
            (Path(folder) / "Drafts.v2" / "Notes").mkdir(parents=True)
            self.assertEqual(app.browse_documents("Drafts.v2/Notes")["parent"], "Drafts.v2")
            self.assertEqual(app.browse_documents("Drafts.v2")["parent"], "")
            self.assertIsNone(app.browse_documents()["parent"])

    def test_group_toggle_validates_all_names_before_changing_settings(self):
        definitions = [{"function": {"name": n}} for n in ("first", "second")]
        with tempfile.TemporaryDirectory() as directory, patch.object(
            storage, "DB_PATH", Path(directory) / "test.db"
        ), patch.object(app, "get_all_tool_definitions", return_value=definitions):
            storage.init_db()
            app.update_tool_settings({"names": ["first", "second"], "enabled": False})
            with self.assertRaises(HTTPException):
                app.update_tool_settings({"names": ["first", "missing"], "enabled": True})
            self.assertEqual(storage.get_tool_settings(), {"first": False, "second": False})
