"""Offline tests of the manual external provider handoff and stale-edit boundary."""
import json
import tempfile
import unittest
from unittest.mock import patch

from coding_checkpoints import CheckpointStore
from external_development import export_prompt, import_response, snapshot_version, decode_response, suggested_paths
from terminal_runner import CodingError


class FakeWorkspace:
    result = {"exit_code": 1, "stdout": "", "stderr": "AssertionError: fixture failure"}

    def __init__(self, files, stop):
        self.files = dict(files)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def snapshot(self):
        return dict(self.files)

    def command(self, argv, timeout):
        assert argv == ["python", "scripts/check.py"]
        return dict(self.result)


class ExternalDevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = CheckpointStore(self.temp.name)
        self.base = {"chat.py": "answer = False\n", "static/app.js": "let state = 0;\n",
                     "scripts/check.py": "pass\n"}
        self.source = lambda: ("a" * 40, self.base)
        self.fetch = lambda n: {"number": n, "title": "Fix chat",
                                 "body": "## Implementation directions\n1. Modify chat.py and static/app.js."}

    def export(self, identifier=None, paths=None):
        return export_prompt(30, identifier, paths, store=self.store, source=self.source, fetch=self.fetch)

    def response(self, package, edits, summary="Fixture"):
        return json.dumps({**{k: package[k] for k in
                              ("session_id", "base_commit", "workspace_version", "package_id")},
                           "edits": edits, "summary": summary})

    def submit(self, package, edits, provider="Claude", summary="Fixture"):
        return import_response(30, package["session_id"], self.response(package, edits, summary),
                               provider, store=self.store, source=self.source, workspace_factory=FakeWorkspace)

    def test_handoff_preserves_latest_workspace_and_failed_checks(self):
        first = self.export()
        self.assertEqual(set(first["paths"]), {"chat.py", "static/app.js"})
        self.assertNotIn("scripts/check.py", first["paths"])
        result = self.submit(first, [{"operation": "replace", "path": "chat.py",
                                      "old": "False", "new": "True"}])
        self.assertEqual(result["status"], "checks_failed")
        self.assertNotEqual(result["workspace_version"], first["workspace_version"])
        second = self.export(first["session_id"])
        self.assertEqual(second["workspace_version"], result["workspace_version"])
        self.assertIn("answer = True", second["prompt"])
        self.assertIn("fixture failure", second["prompt"])
        self.assertIn("Claude", second["prompt"])
        result2 = self.submit(second, [{"operation": "replace", "path": "static/app.js",
                                       "old": "state = 0", "new": "state = 1"}], "DeepSeek")
        self.assertEqual(set(result2["changed_paths"]), {"chat.py", "static/app.js"})
        self.assertIn("+answer = True", result2["patch"])
        self.assertIn("+let state = 1;", result2["patch"])
        self.assertEqual(self.base["chat.py"], "answer = False\n")
        self.assertEqual(self.store.read(first["session_id"])["files"]["chat.py"], "answer = True\n")

    def test_switching_provider_invalidates_prior_prompt(self):
        first = self.export()
        second = self.export(first["session_id"])
        self.assertEqual(first["workspace_version"], second["workspace_version"])
        self.assertNotEqual(first["package_id"], second["package_id"])
        with self.assertRaisesRegex(CodingError, "Stale"):
            self.submit(first, [{"operation": "replace", "path": "chat.py", "old": "False", "new": "True"}])
        self.submit(second, [{"operation": "replace", "path": "chat.py", "old": "False", "new": "True"}])
        with self.assertRaisesRegex(CodingError, "Stale"):
            self.submit(second, [])

    def test_protected_paths_and_atomic_validation(self):
        first = self.export(paths=["chat.py"])
        for invalid in [
            {"operation": "replace", "path": "scripts/check.py", "old": "pass", "new": "exit()"},
            {"operation": "create_file", "path": "../outside.py", "new": "pass"},
            {"operation": "replace", "path": "static/app.js", "old": "0", "new": "1"},
            {"operation": "replace", "path": "chat.py", "old": "missing", "new": "True"},
        ]:
            with self.assertRaises(CodingError):
                self.submit(first, [{"operation": "replace", "path": "chat.py",
                                     "old": "False", "new": "True"}, invalid])
            self.assertEqual(self.store.read(first["session_id"])["files"], self.base)

    def test_blocked_response_becomes_handoff(self):
        first = self.export()
        result = self.submit(first, [], summary="Need another file")
        self.assertEqual(result["status"], "no_changes")
        second = self.export(first["session_id"])
        self.assertIn("Need another file", second["prompt"])
        self.assertEqual(first["workspace_version"], second["workspace_version"])

    def test_base_commit_mismatch_rejected(self):
        first = self.export()
        self.source = lambda: ("b" * 40, self.base)
        with self.assertRaisesRegex(CodingError, "base commit"):
            self.export(first["session_id"])

    def test_new_file_included_on_next_export(self):
        first = self.export()
        self.submit(first, [{"operation": "create_file", "path": "tests/test_new.py", "new": "pass\n"}])
        second = self.export(first["session_id"])
        self.assertIn("tests/test_new.py", second["paths"])
        self.assertIn("tests/test_new.py", second["changed_paths"])


    def test_automatic_scope_uses_handoff_and_live_dependencies(self):
        self.base.update({
            "chat.py": "from storage import save_message\nanswer = False\n",
            "storage.py": "def save_message(): pass\n",
            "app.py": "from chat import answer\n",
            "static/service-worker.js": "const CACHE = 'v1';\n",
            "docs/DATA_FLOW.md": "## Chat request\n`chat.py` loads from `storage.py`.\n",
        })
        first = self.export(paths=["chat.py", "static/app.js"])
        self.submit(first, [{"operation": "replace", "path": "chat.py",
                            "old": "False", "new": "True"}],
                    summary="Reconnect works. app.py and static/service-worker.js were not exported.")
        next_prompt = export_prompt(30, first["session_id"], focus="Durable run state after restart",
                                    store=self.store, source=self.source, fetch=self.fetch)
        self.assertTrue(next_prompt["automatic"])
        for name in ("chat.py", "static/app.js", "storage.py", "app.py", "static/service-worker.js"):
            self.assertIn(name, next_prompt["paths"])
        self.assertIn("answer = True", next_prompt["prompt"])

    def test_automatic_scope_reports_files_omitted_by_budget(self):
        self.base.update({"storage.py": "a" * 300000, "app.py": "b" * 300000})
        self.fetch = lambda n: {"number": n, "title": "Persistence", "body": "Modify chat.py, storage.py and app.py."}
        with patch("external_development.MAX_PROMPT_BYTES", 20000):
            result = self.export()
        self.assertIn("chat.py", result["paths"])
        self.assertIn("storage.py", result["omitted_paths"])
        self.assertIn("app.py", result["omitted_paths"])
        self.assertLessEqual(len(result["prompt"].encode("utf-8")), 20000)

    def test_manual_selection_remains_exact(self):
        self.base["storage.py"] = "def save_message(): pass\n"
        self.base["chat.py"] = "from storage import save_message\n"
        result = self.export(paths=["chat.py"])
        self.assertFalse(result["automatic"])
        self.assertEqual(result["paths"], ["chat.py"])
        self.assertIn("storage.py", result["omitted_paths"])

    def test_updated_coordinator_pins_original_session_base(self):
        first = self.export()
        self.submit(first, [{"operation": "replace", "path": "chat.py",
                            "old": "False", "new": "True"}])
        with patch("external_development.committed_source", return_value=("a" * 40, self.base)) as read:
            next_prompt = export_prompt(30, first["session_id"], store=self.store, fetch=self.fetch)
        read.assert_called_once_with(revision="a" * 40)
        self.assertEqual(next_prompt["base_commit"], first["base_commit"])
        self.assertIn("answer = True", next_prompt["prompt"])

    def test_json_fence_and_duplicate_keys(self):
        package = self.export()
        raw = self.response(package, [])
        self.assertEqual(decode_response("```json\n" + raw + "\n```")["edits"], [])
        with self.assertRaises(CodingError):
            decode_response('{"edits":[],"edits":[]}')


if __name__ == "__main__":
    unittest.main()
