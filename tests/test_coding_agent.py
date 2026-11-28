"""Offline regression tests for the issue-to-patch loop and source boundaries."""
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from coding_agent import execute_action, run_issue, rate_limit_delay, MODEL
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


def completion(args=None, text=None):
    call = [] if args is None else [SimpleNamespace(
        id="call-1", function=SimpleNamespace(name="coding_action", arguments=json.dumps(args))
    )]
    message = Mock(content=text, tool_calls=call)
    message.model_dump.return_value = {"role": "assistant", "content": text or ""}
    return SimpleNamespace(choices=[SimpleNamespace(message=message)],
                           usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5))


class CodingTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
