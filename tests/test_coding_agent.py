"""Offline regression tests for the issue-to-patch loop and source boundaries."""
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from coding_agent import execute_action, run_issue, rate_limit_delay, MODEL, issue_plan, report_plan_progress, pending_plan, TOOLS, compact_messages, provider_error_category
from coding_sandbox.policy import allowed_source
from terminal_runner import make_patch, verified_snapshot, CodingError


class FakeWorkspace:
    def __init__(self, files, stop):
        self.files = dict(files)
        self.commands = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def action(self, operation, **args):
        if operation == "read_file":
            return self.files[args["path"]]
        if operation == "replace":
            self.files[args["path"]] = self.files[args["path"]].replace(args["old"], args["new"])
            return {"changed": args["path"]}
        return {}

    def command(self, argv, timeout=60):
        self.commands.append(argv)
        return {"exit_code": 0, "stdout": "fixture checks", "stderr": "", "truncated": False}

    def snapshot(self):
        return dict(self.files)


def completion(args=None, text=None, tool="coding_action"):
    call = [] if args is None else [SimpleNamespace(
        id="call-1", function=SimpleNamespace(name=tool, arguments=json.dumps(args))
    )]
    message = Mock(content=text, tool_calls=call)
    message.model_dump.return_value = {"role": "assistant", "content": text or ""}
    return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


class CodingTests(unittest.TestCase):
    def test_issue_directions_and_coverage(self):
        issue = {"body": "## Implementation directions\n1. Old direction\n"
                 "## Acceptance criteria\n- [x] Prior work\n- [ ] Test the new change\n"
                 "## Next change on the coding branch\n1. Read chat.py\n2. Edit and test it\n"}
        plan = issue_plan(issue)
        self.assertEqual([step["id"] for step in plan], ["D1", "D2", "A1"])
        self.assertEqual(plan[0]["text"], "Read chat.py")
        reports = {}
        self.assertIn("error", report_plan_progress(plan, reports,
            {"step_id": "invented", "status": "reported_done", "evidence": "claim"}))
        result = report_plan_progress(plan, reports,
            {"step_id": "D1", "status": "reported_done", "evidence": "Read chat.py"})
        self.assertFalse(result["verified"])
        self.assertEqual(pending_plan(plan, reports), ["D2", "A1"])

    def test_passing_checks_do_not_complete_unreported_issue_plan(self):
        before = {"chat.py": "return False\n", "scripts/check.py": "pass\n"}
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion({"path": "chat.py", "old": "False", "new": "True"}, tool="coding_replace"),
            completion(text="Done."), completion(text="Done."), completion(text="Done."),
        ]
        result = run_issue(123, threading.Event(), Mock(),
            fetch=lambda _: {"number": 123, "title": "Fixture",
                "body": "## Implementation directions\n1. Fix result\n## Acceptance criteria\n- [ ] Verify result"},
            source=lambda: ("a" * 40, before), workspace_factory=FakeWorkspace,
            client_factory=lambda: client, usage=Mock(), pause=Mock(return_value=True))
        self.assertEqual(result["status"], "partial_review")
        self.assertEqual(result["checks"]["exit_code"], 0)
        self.assertEqual(result["remaining_plan_items"], ["D1", "A1"])

    def test_dedicated_tools_and_scoped_search(self):
        names = {entry["function"]["name"] for entry in TOOLS}
        self.assertNotIn("coding_action", names)
        self.assertIn("coding_search", names)
        self.assertIn("coding_run_tests", names)
        workspace = FakeWorkspace({"chat.py": "needle\n", "other.py": "needle\n"}, threading.Event())
        self.assertEqual(execute_action(workspace, {"operation": "search", "query": "needle",
                                                   "path": "chat.py"})[0]["path"], "chat.py")
        self.assertEqual(execute_action(workspace, {"operation": "read_file", "path": "chat.py",
                                                   "start_line": None}), "needle\n")
        execute_action(workspace, {"operation": "run_command", "argv": [".venv/bin/python", "-m", "unittest"]})
        self.assertEqual(workspace.commands[-1][0], "python")
        execute_action(workspace, {"operation": "run_tests", "path": "tests/test_chat.py"})
        self.assertEqual(workspace.commands[-1], ["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_chat.py"])
        self.assertIn("error", execute_action(workspace, {"operation": "run_tests", "path": "../test_chat.py"}))

    def test_context_compacts_complete_tool_turns(self):
        messages = [{"role": "system", "content": "rules"}, {"role": "user", "content": "issue"}]
        for i in range(8):
            messages.extend([{"role": "assistant", "tool_calls": [{"id": str(i)}]},
                             {"role": "tool", "tool_call_id": str(i), "content": "x" * 1000}])
        compacted = compact_messages(messages, {}, [], ["chat.py"], [], None)
        self.assertEqual(compacted[:2], messages[:2])
        self.assertEqual([m["tool_call_id"] for m in compacted if m["role"] == "tool"], ["5", "6", "7"])
        self.assertIn("chat.py", compacted[2]["content"])
        self.assertLess(len(str(compacted)), len(str(messages)))

    def test_unsupported_provider_error_is_sanitized(self):
        error = SimpleNamespace(body={"error": {"code": "tool_use_failed", "message": "private payload"}})
        self.assertEqual(provider_error_category(error), "tool_use_failed")
        self.assertEqual(provider_error_category(SimpleNamespace(body={"code": "secret"})), "unclassified")

    def test_false_completion_does_not_survive_checkpoint(self):
        before = {"chat.py": "return False\n", "scripts/check.py": "pass\n"}
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion({"path": "chat.py", "old": "False", "new": "True"}, tool="coding_replace"),
            completion({"step_id": "D1", "status": "reported_done", "evidence": "Edited missing.py"},
                       tool="coding_report_progress"),
            completion({"step_id": "A1", "status": "reported_done", "evidence": "chat.py"},
                       tool="coding_report_progress"),
            completion(text="Done."), completion(text="Done."), completion(text="Done."),
        ]
        checkpoint = Mock()
        result = run_issue(123, threading.Event(), Mock(),
            fetch=lambda _: {"number": 123, "title": "Fixture",
                "body": "## Implementation directions\n1. Fix result\n## Acceptance criteria\n- [ ] Verify result"},
            source=lambda: ("a" * 40, before), workspace_factory=FakeWorkspace,
            client_factory=lambda: client, usage=Mock(), pause=Mock(return_value=True), checkpoint=checkpoint)
        self.assertEqual(result["status"], "partial_review")
        self.assertEqual(result["remaining_plan_items"], ["D1", "A1"])
        self.assertEqual(checkpoint.call_args_list[0].args[2]["reason"], "edit saved")
        self.assertEqual(checkpoint.call_args_list[0].args[1]["chat.py"], "return True\n")

    def test_source_allowlist(self):
        for path in ("chat.py", "docs/README.md", "static/app.js", "tests/test_chat.py"):
            self.assertTrue(allowed_source(path))
        for path in (".env", "/etc/passwd", "../chat.py", "docs/../secret.py",
                     "google_calendar_token.json", "conversations.db", "dist/backend",
                     "static/.git/config", "static/node_modules/x.js"):
            self.assertFalse(allowed_source(path), path)
        with self.assertRaises(CodingError):
            verified_snapshot({".env": "secret"})

    def test_host_diff(self):
        patch = make_patch({"chat.py": "before\n"}, {"chat.py": "after\n"})
        self.assertIn("-before\n+after\n", patch)
        self.assertIn("diff --git a/chat.py b/chat.py", patch)

    def test_protected_operations(self):
        workspace = Mock()
        self.assertIn("error", execute_action(workspace, {"operation": "snapshot"}))
        self.assertIn("error", execute_action(workspace, {
            "operation": "replace", "path": "scripts/check.py", "old": "a", "new": "b"}))
        workspace.action.assert_not_called()

    def test_flash_lite_issue_to_patch(self):
        before = {"chat.py": "return False\n", "scripts/check.py": "pass\n"}
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion({"operation": "read_file", "path": "chat.py"}),
            completion({"operation": "replace", "path": "chat.py", "old": "False", "new": "True"}),
            completion({"operation": "run_command", "argv": ["python", "-m", "unittest"]}),
            completion(text="Fixed fixture."),
        ]
        usage = Mock()
        workspace = FakeWorkspace(before, threading.Event())
        result = run_issue(123, threading.Event(), Mock(),
                           fetch=lambda _: {"number": 123, "title": "Fixture", "body": "Fix result."},
                           source=lambda: ("a" * 40, before),
                           workspace_factory=lambda *_: workspace,
                           client_factory=lambda: client, usage=usage, pause=Mock(return_value=True))
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["usage"]["requests"], 4)
        self.assertEqual(result["changed_paths"], ["chat.py"])
        self.assertIn("+return True", result["patch"])
        self.assertEqual(before["chat.py"], "return False\n")
        self.assertEqual(workspace.commands[-1], ["python", "scripts/check.py"])
        self.assertTrue(all(c.kwargs["model"] == MODEL
                            for c in client.chat.completions.create.call_args_list))
        usage.assert_called_with(40, 20, final=True)

    def test_rate_limit_retry_keeps_edits_and_does_not_replay_tools(self):
        before = {"chat.py": "return False\n", "scripts/check.py": "pass\n"}
        error = RuntimeError("rate limited")
        error.status_code = 429
        error.response = SimpleNamespace(headers={"Retry-After": "4"})
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion({"operation": "replace", "path": "chat.py", "old": "False", "new": "True"}),
            error,
            completion(text="Fixed."),
        ]
        workspace = FakeWorkspace(before, threading.Event())
        pause = Mock(return_value=True)
        result = run_issue(123, threading.Event(), Mock(),
                           fetch=lambda _: {"number": 123, "title": "Fixture", "body": "Fix result."},
                           source=lambda: ("a" * 40, before),
                           workspace_factory=lambda *_: workspace,
                           client_factory=lambda: client, usage=Mock(), pause=pause)
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["usage"]["attempts"], 3)
        self.assertEqual(result["usage"]["requests"], 2)
        self.assertEqual(result["usage"]["rate_retries"], 1)
        self.assertIn("+return True", result["patch"])
        self.assertTrue(any(c.args[1] == 65 for c in pause.call_args_list))
        self.assertEqual(before["chat.py"], "return False\n")

    def test_daily_rate_limit_returns_partial_patch(self):
        before = {"chat.py": "return False\n", "scripts/check.py": "pass\n"}
        error = RuntimeError("daily quota")
        error.status_code = 429
        error.body = {"error": {"details": [{"violations": [
            {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion({"operation": "replace", "path": "chat.py", "old": "False", "new": "True"}),
            error,
        ]
        result = run_issue(123, threading.Event(), Mock(),
                           fetch=lambda _: {"number": 123, "title": "Fixture", "body": "Fix result."},
                           source=lambda: ("a" * 40, before),
                           workspace_factory=FakeWorkspace,
                           client_factory=lambda: client, usage=Mock(), pause=Mock(return_value=True))
        self.assertEqual(result["status"], "partial_review")
        self.assertIn("+return True", result["patch"])
        self.assertEqual(result["usage"]["rate_retries"], 0)
        self.assertEqual(client.chat.completions.create.call_count, 2)

    def test_retry_delay_is_bounded(self):
        error = SimpleNamespace(body=None, response=SimpleNamespace(headers={"Retry-After": "3600"}))
        self.assertIsNone(rate_limit_delay(error))
        error.response.headers = {"Retry-After": "70"}
        self.assertEqual(rate_limit_delay(error), 70)

    def test_no_change_completion_is_rejected_then_dedicated_edit_works(self):
        before = {"chat.py": "return False\n", "scripts/check.py": "pass\n"}
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion(text="I can provide the changes if requested."),
            completion({"path": "chat.py", "old": "False", "new": "True"}, tool="coding_replace"),
            completion(text="Implemented."),
        ]
        emit = Mock()
        result = run_issue(123, threading.Event(), emit,
                           fetch=lambda _: {"number": 123, "title": "Fixture", "body": "Fix result."},
                           source=lambda: ("a" * 40, before),
                           workspace_factory=FakeWorkspace,
                           client_factory=lambda: client, usage=Mock(), pause=Mock(return_value=True))
        self.assertEqual(result["status"], "review")
        self.assertIn("+return True", result["patch"])
        self.assertEqual(result["usage"]["requests"], 3)
        self.assertTrue(any("Completion rejected" in str(c) for c in emit.call_args_list))

    def test_unproductive_model_stops_after_bounded_reminders(self):
        before = {"chat.py": "return False\n", "scripts/check.py": "pass\n"}
        client = Mock()
        client.chat.completions.create.side_effect = [
            completion(text="No edits."), completion(text="No edits."), completion(text="No edits."),
        ]
        result = run_issue(123, threading.Event(), Mock(),
                           fetch=lambda _: {"number": 123, "title": "Fixture", "body": "Fix result."},
                           source=lambda: ("a" * 40, before),
                           workspace_factory=FakeWorkspace,
                           client_factory=lambda: client, usage=Mock(), pause=Mock(return_value=True))
        self.assertEqual(result["status"], "no_changes")
        self.assertEqual(result["reason"], "model stopped without implementing a change")
        self.assertEqual(client.chat.completions.create.call_count, 3)


if __name__ == "__main__":
    unittest.main()
