"""Offline regression tests for the issue-to-patch loop and source boundaries."""
import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from coding_agent import execute_action, run_issue, MODEL
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
                           client_factory=lambda: client, usage=usage)
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["usage"]["requests"], 4)
        self.assertEqual(result["changed_paths"], ["chat.py"])
        self.assertIn("+return True", result["patch"])
        self.assertEqual(before["chat.py"], "return False\n")
        self.assertEqual(workspace.commands[-1], ["python", "scripts/check.py"])
        self.assertTrue(all(c.kwargs["model"] == MODEL
                            for c in client.chat.completions.create.call_args_list))
        usage.assert_called_with(40, 20, final=True)


if __name__ == "__main__":
    unittest.main()
