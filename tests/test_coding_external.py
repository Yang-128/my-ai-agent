"""Offline tests of the manual external provider handoff and stale-edit boundary."""
import json
import tempfile
import unittest
from unittest.mock import patch

from coding_checkpoints import CheckpointStore
from external_development import export_prompt, import_response, snapshot_version, decode_response, suggested_paths, apply_edits, saved_patch
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


    def test_compact_profile_uses_exact_relevant_excerpts(self):
        filler = "# unrelated filler " + "x" * 140 + "\n"
        self.base["app.py"] = (filler * 550 + "def approve_calendar_event():\n"
                               "    return 'calendar validation'\n" + filler * 300)
        self.base["tests/test_run_recovery.py"] = "def test_approval():\n    assert True\n"
        self.fetch = lambda n: {"number": n, "title": "Calendar", "body": "Improve app.py."}
        result = export_prompt(30, focus="Repair approve_calendar_event in app.py and tests/test_run_recovery.py.",
                               profile="deepseek", store=self.store, source=self.source, fetch=self.fetch)
        self.assertLessEqual(result["prompt_bytes"], 64000)
        self.assertIn("app.py", result["partial_paths"])
        self.assertIn("tests/test_run_recovery.py", result["paths"])
        self.assertIn("return 'calendar validation'", result["prompt"])
        marker = self.store.read(result["session_id"])["history"][-1]
        self.assertTrue(any("return 'calendar validation'" in excerpt
                            for excerpt in marker["exported_excerpts"]["app.py"]))
        updated = self.submit(result, [{"operation": "replace", "path": "app.py",
                                       "old": "return 'calendar validation'", "new": "return 'fixed'"}])
        self.assertIn("app.py", updated["changed_paths"])

    def test_excerpt_import_rejects_hidden_code_and_accepts_ordered_edits(self):
        files = {"app.py": "visible = 1\nhidden = 2\n"}
        excerpts = {"app.py": ["visible = 1\n"]}
        with self.assertRaisesRegex(CodingError, "exported source excerpt"):
            apply_edits(files, [{"operation": "replace", "path": "app.py",
                                 "old": "hidden = 2", "new": "hidden = 3"}], ["app.py"], excerpts)
        updated = apply_edits(files, [
            {"operation": "replace", "path": "app.py", "old": "visible = 1", "new": "visible = 3"},
            {"operation": "replace", "path": "app.py", "old": "visible = 3", "new": "visible = 4"},
        ], ["app.py"], excerpts)
        self.assertEqual(updated["app.py"], "visible = 4\nhidden = 2\n")
        self.assertEqual(files["app.py"], "visible = 1\nhidden = 2\n")

    def test_compact_failed_checks_prioritize_traceback_files(self):
        self.base.update({"app.py": "def approve():\n    return False\n",
                          "tests/test_run_recovery.py": "def test_approve():\n    assert False\n"})
        first = self.export()
        result = self.store.read(first["session_id"])
        trace = ('ERROR: test_approve\nTraceback (most recent call last):\n'
                 '  File "/workspace/tests/test_run_recovery.py", line 2, in test_approve\n'
                 '  File "/workspace/app.py", line 2, in approve\n'
                 'ValueError: Calendar time entries must match the pending proposals.\n')
        result["history"].append({"reason": "external checks finished", "checks": {"exit_code": 1, "stderr": trace}})
        self.store.save(first["session_id"], 30, first["base_commit"], result["files"], result["history"])
        repair = export_prompt(30, first["session_id"], profile="small", store=self.store,
                               source=self.source, fetch=self.fetch)
        self.assertEqual(repair["paths"][:2], ["tests/test_run_recovery.py", "app.py"])
        self.assertIn("Repair the failed checks before adding features", repair["prompt"])
        self.assertLessEqual(repair["prompt_bytes"], 32000)

    def test_blocked_handoff_prioritizes_missing_files_automatically(self):
        self.base["storage.py"] = "def recover(): pass\n"
        first = self.export()
        self.submit(first, [], summary="Need storage.py to check recovery.")
        second = export_prompt(30, first["session_id"], profile="deepseek", store=self.store,
                               source=self.source, fetch=self.fetch)
        self.assertEqual(second["paths"][0], "storage.py")
        self.assertEqual(second["workspace_version"], first["workspace_version"])


    def test_compact_js_handoff_exports_named_functions_not_deferred_work(self):
        filler = "// unrelated source " + "x" * 140 + "\n"
        self.base["static/app.js"] = (
            filler * 440 +
            "async function openConversation(id) {\n"
            "  await loadConversation(id);\n"
            "}\n"
            "async function sendMessage(text) {\n"
            "  await fetch('/api/chat');\n"
            "  await readProgressStream();\n"
            "}\n"
            "async function readProgressStream() {\n"
            "  return 'stream reader';\n"
            "}\n" + filler * 350
        )
        self.base["chat.py"] = filler.replace("//", "#") * 600
        self.base["docs/CHAT_PROGRESS.md"] = "Reload recovery is not implemented.\n"
        self.fetch = lambda n: {"number": n, "title": "Progress", "body": "Improve chat.py and static/app.js."}
        first = self.export(paths=["docs/CHAT_PROGRESS.md"])
        self.submit(first, [], summary=(
            "Blocked. Exact files needed: static/app.js with sendMessage, "
            "readProgressStream and conversation-load path; docs/CHAT_PROGRESS.md. "
            "Follow-on milestone: chat.py in full."
        ))
        second = export_prompt(30, first["session_id"], profile="small", store=self.store,
                               source=self.source, fetch=self.fetch)
        self.assertIn("static/app.js", second["partial_paths"])
        self.assertIn("docs/CHAT_PROGRESS.md", second["paths"])
        package = json.loads(second["prompt"].split("PROJECT DATA (JSON):\n", 1)[1])
        excerpts = "\n".join(e["text"] for e in package["source_excerpts"]["static/app.js"])
        self.assertIn("async function sendMessage(text)", excerpts)
        self.assertIn("await readProgressStream();", excerpts)
        self.assertIn("async function readProgressStream()", excerpts)
        self.assertIn("return 'stream reader';", excerpts)
        self.assertIn("async function openConversation(id)", excerpts)
        self.assertLessEqual(second["prompt_bytes"], 32000)
        self.assertEqual(first["workspace_version"], second["workspace_version"])


    def test_feature_scope_prioritizes_unfinished_frontend_after_passing_checks(self):
        filler = "// unrelated " + "x" * 140 + "\n"
        self.base["static/app.js"] = (
            filler * 600 +
            "async function openConversation(id) {\n"
            "  return await fetch('/api/conversations/' + id);\n"
            "}\n"
            "async function readProgressStream() {\n"
            "  return 'stream reader';\n"
            "}\n" + filler * 300
        )
        first = self.export(paths=["chat.py"])
        with patch.object(FakeWorkspace, "result", {"exit_code": 0, "stdout": "OK", "stderr": ""}):
            result = self.submit(first, [{"operation": "replace", "path": "chat.py",
                                         "old": "False", "new": "True"}],
                                 summary="Backend complete. Frontend is still blocked on static/app.js conversation-load path. Reuse readProgressStream. Follow-on milestone: chat.py internal tools.")
        self.assertEqual(result["status"], "partial_review")
        second = export_prompt(30, first["session_id"], profile="small", work_scope="feature",
                               store=self.store, source=self.source, fetch=self.fetch)
        package = json.loads(second["prompt"].split("PROJECT DATA (JSON):\n", 1)[1])
        self.assertEqual(second["suggested_paths"][0], "static/app.js")
        self.assertIn("Finish the remaining user-facing behavior", package["focus"])
        self.assertIn("Complete as much of one coherent end-to-end feature", second["prompt"])
        excerpts = "\n".join(e["text"] for e in package["source_excerpts"]["static/app.js"])
        self.assertIn("async function openConversation(id)", excerpts)
        self.assertIn("async function readProgressStream()", excerpts)
        self.assertLessEqual(second["prompt_bytes"], 32000)
        self.assertEqual(second["workspace_version"], result["workspace_version"])

    def test_feature_scope_still_prioritizes_failed_checks(self):
        first = self.export()
        self.submit(first, [{"operation": "replace", "path": "chat.py", "old": "False", "new": "True"}])
        second = export_prompt(30, first["session_id"], work_scope="feature",
                               store=self.store, source=self.source, fetch=self.fetch)
        self.assertIn("Repair the failed checks before adding features", second["prompt"])
        self.assertNotIn("Complete as much of one coherent end-to-end feature", second["prompt"])

    def test_response_validation_reports_specific_schema_errors(self):
        package = self.export()
        valid = json.loads(self.response(package, []))
        cases = [
            ({k: v for k, v in valid.items() if k != "package_id"}, "missing fields: package_id"),
            ({**valid, "unexpected": "private content"}, "1 unexpected top-level field"),
            ({**valid, "edits": {}}, "edits must be a JSON array"),
            ({**valid, "edits": [{}] * 41}, "41 edits supplied; maximum 40"),
            ({**valid, "summary": "x" * 12001}, "summary is 12001 characters; maximum 12000"),
            ({**valid, "summary": []}, "summary must be a JSON string"),
            ({**valid, "package_id": None}, "package_id must be a JSON string"),
            ([], "single JSON object"),
        ]
        for payload, diagnostic in cases:
            with self.subTest(diagnostic=diagnostic):
                with self.assertRaisesRegex(CodingError, diagnostic):
                    decode_response(json.dumps(payload))
        self.assertEqual(decode_response(json.dumps(valid)), valid)

    def test_long_summary_preserved_across_external_rounds(self):
        package = self.export()
        summary = "x" * 4146
        result = self.submit(package, [], summary=summary)
        self.assertEqual(result["status"], "no_changes")
        self.assertEqual(result["summary"], summary)
        next_package = self.export(package["session_id"])
        payload = json.loads(next_package["prompt"].split("PROJECT DATA (JSON):\n", 1)[1])
        self.assertEqual(payload["last_summary"], summary)
        self.assertEqual(decode_response(self.response(next_package, [], summary="x" * 12000))["summary"], "x" * 12000)

    def test_external_roadmap_persists_reported_coverage_and_legacy_import(self):
        self.fetch = lambda n: {"number": n, "title": "Progress",
            "body": "## Implementation directions\n1. Backend in chat.py\n2. Frontend in static/app.js\n## Definition of done\n- Verify behavior"}
        first = self.export()
        self.assertEqual(first["issue_progress"]["total"], 3)
        self.assertEqual(first["issue_progress"]["reported_done"], 0)
        payload = json.loads(self.response(first, [{"operation": "replace", "path": "chat.py",
                                                    "old": "False", "new": "True"}]))
        payload["progress"] = {"current_id": "D2", "current_task": "Finish frontend",
                               "reports": [{"step_id": "D1", "status": "reported_done",
                                            "evidence": "Backend implementation in chat.py"}]}
        with patch.object(FakeWorkspace, "result", {"exit_code": 0, "stdout": "OK", "stderr": ""}):
            result = import_response(30, first["session_id"], json.dumps(payload), "DeepSeek",
                                     store=self.store, source=self.source, workspace_factory=FakeWorkspace)
        roadmap = result["issue_progress"]
        self.assertEqual(roadmap["reported_done"], 1)
        self.assertEqual(roadmap["current_id"], "D2")
        self.assertFalse(roadmap["verified"])
        self.assertEqual(self.store.list()[0]["issue_progress"], roadmap)
        second = self.export(first["session_id"])
        self.assertEqual(second["issue_progress"]["current_task"], "Finish frontend")
        legacy = self.submit(second, [], summary="No edits this round")
        self.assertEqual(legacy["issue_progress"]["reported_done"], 1)

    def test_external_roadmap_rejects_unknown_ids_before_saving(self):
        first = self.export()
        payload = json.loads(self.response(first, []))
        payload["progress"] = {"current_id": "D999", "current_task": "Fake milestone", "reports": []}
        with self.assertRaisesRegex(CodingError, "current_id"):
            import_response(30, first["session_id"], json.dumps(payload), "DeepSeek",
                            store=self.store, source=self.source, workspace_factory=FakeWorkspace)
        self.assertEqual(self.store.read(first["session_id"])["history"][-1]["package_id"], first["package_id"])

    def test_saved_patch_reconstructs_latest_diff_without_invalidating_export(self):
        first = self.export()
        with patch.object(FakeWorkspace, "result", {"exit_code": 0, "stdout": "OK", "stderr": ""}):
            imported = self.submit(first, [{"operation": "replace", "path": "chat.py",
                                            "old": "False", "new": "True"}])
        next_package = self.export(first["session_id"])
        before = self.store.read(first["session_id"])
        restored = saved_patch(first["session_id"], store=self.store, source=self.source)
        self.assertEqual(restored["patch"], imported["patch"])
        self.assertEqual(restored["source_commit"], first["base_commit"])
        self.assertEqual(restored["workspace_version"], imported["workspace_version"])
        self.assertEqual(restored["checks"]["exit_code"], 0)
        self.assertEqual(restored["changed_paths"], ["chat.py"])
        self.assertEqual(self.store.read(first["session_id"]), before)
        self.assertEqual(before["history"][-1]["package_id"], next_package["package_id"])

    def test_json_fence_and_duplicate_keys(self):
        package = self.export()
        raw = self.response(package, [])
        self.assertEqual(decode_response("```json\n" + raw + "\n```")["edits"], [])
        with self.assertRaises(CodingError):
            decode_response('{"edits":[],"edits":[]}')


if __name__ == "__main__":
    unittest.main()
