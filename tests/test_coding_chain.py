"""Offline tests for free-model selection and durable coding handoffs."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from coding_chain import free_candidates, run_chain
from coding_checkpoints import CheckpointStore
from terminal_runner import CodingError, make_patch


BASE = {"chat.py": "first = False\nsecond = False\n",
        "scripts/check.py": "pass\n"}
REV = "a" * 40
FIRST = {"provider": "gemini", "model": "gemini-3.5-flash-lite"}
SECOND = {"provider": "groq", "model": "openai/gpt-oss-120b"}


class CodingChainTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CheckpointStore(Path(self.temp.name) / "checkpoints")
        self.validate = Mock()

    def stage(self, number, stop, emit, **kwargs):
        revision, original = kwargs["source"]()
        files = dict(kwargs["initial"] or original)
        model = kwargs["model"]
        if model == FIRST["model"]:
            files["chat.py"] = files["chat.py"].replace("first = False", "first = True")
            status, reason = "partial_review", "coding budget reached"
        else:
            self.assertIn("first = True", files["chat.py"])
            self.assertEqual(kwargs["handoff"][-1]["model"], FIRST["model"])
            files["chat.py"] = files["chat.py"].replace("second = False", "second = True")
            status, reason = "review", "model finished"
        kwargs["checkpoint"](revision, files, {"model": model, "provider": kwargs["provider"],
            "reason": reason, "changed_paths": ["chat.py"], "checks": {"exit_code": 0}})
        return {"status": status, "reason": reason, "patch": make_patch(original, files),
                "source_commit": revision}

    def run_fixture(self, selections, checkpoint_id=None):
        return run_chain(30, threading.Event(), Mock(), selections, checkpoint_id,
                         store=self.store, source=lambda: (REV, BASE),
                         stage=self.stage, validate=self.validate,
                         interval_for=lambda p, m: 0, client=Mock())

    def test_handoff_across_providers(self):
        result = self.run_fixture([FIRST, SECOND])
        self.assertEqual(result["status"], "review")
        self.assertIn("+first = True", result["patch"])
        self.assertIn("+second = True", result["patch"])
        self.assertEqual(len(result["stages"]), 2)
        self.assertEqual(self.validate.call_count, 2)
        self.assertIn("first = False", BASE["chat.py"])

    def test_resume_after_restart(self):
        first = self.run_fixture([FIRST])
        identifier = first["checkpoint_id"]
        self.store = CheckpointStore(Path(self.temp.name) / "checkpoints")
        self.assertEqual(self.store.list()[0]["id"], identifier)
        result = self.run_fixture([SECOND], identifier)
        self.assertEqual(result["checkpoint_id"], identifier)
        self.assertIn("+second = True", result["patch"])
        self.store.delete(identifier)
        self.assertEqual(self.store.list(), [])

    def test_checkpoint_different_base_rejected(self):
        identifier = self.run_fixture([FIRST])["checkpoint_id"]
        with self.assertRaises(CodingError):
            run_chain(30, threading.Event(), Mock(), [SECOND], identifier,
                      store=self.store, source=lambda: ("b" * 40, BASE),
                      stage=self.stage, validate=self.validate)

    def test_unverified_candidate_does_not_run(self):
        self.validate.side_effect = CodingError("not free")
        with self.assertRaises(CodingError):
            self.run_fixture([FIRST])
        self.assertEqual(self.store.list(), [])

    def test_duplicate_and_unsupported_models_rejected(self):
        for selections in ([FIRST, FIRST], [{"provider": "paid", "model": "paid"}]):
            with self.assertRaises(CodingError):
                self.run_fixture(selections)

    def test_free_catalog_excludes_paid_non_tool_and_small_context(self):
        models = [
            {"id": "candidate:free", "free": True, "context_length": 65536,
             "capabilities": {"tools": True}},
            {"id": "paid", "free": True, "context_length": 65536,
             "capabilities": {"tools": True}},
            {"id": "not-free:free", "free": False, "context_length": 65536,
             "capabilities": {"tools": True}},
            {"id": "no-tools:free", "free": True, "context_length": 65536,
             "capabilities": {"tools": False}},
            {"id": "small:free", "free": True, "context_length": 1000,
             "capabilities": {"tools": True}},
        ]
        with patch("providers.provider_enabled", return_value=True), \
             patch("providers.provider_key", return_value="fixture"), \
             patch("providers.discover_free_models", return_value=models):
            result = free_candidates("openrouter")
        self.assertEqual([m["model"] for m in result], ["candidate:free"])

    def test_protected_checkpoint_paths(self):
        with self.assertRaises(CodingError):
            self.store.read("../secrets")
        with self.assertRaises(CodingError):
            self.store.save("b" * 32, 30, REV, {".env": "secret"}, [])


if __name__ == "__main__":
    unittest.main()
