"""Offline tests for the portable context exporter; no GitHub credentials needed."""
import contextlib
import io
import unittest
from unittest.mock import patch

from scripts import export_ai_context as export


class ContextExportTests(unittest.TestCase):
    def fake_git(self, *args):
        if args == ("rev-parse", "HEAD"):
            return "a" * 40 + "\n"
        if args == ("branch", "--show-current"):
            return "test-branch\n"
        if args == ("status", "--porcelain", "--untracked-files=normal"):
            return "?? private-local-name\n"
        if args[0] == "show":
            return "Committed reference " + args[1].split(":", 1)[1]
        raise AssertionError(args)

    @patch.object(export, "github")
    def test_default_is_offline_and_does_not_export_worktree_paths(self, github):
        with patch.object(export, "git", side_effect=self.fake_git) as git:
            packet = export.build_packet()
        github.assert_not_called()
        self.assertIn("Working tree has local changes: yes", packet)
        self.assertIn("Committed reference PROJECT_CONTEXT.md", packet)
        self.assertNotIn("private-local-name", packet)
        self.assertEqual(git.call_count, 4)

    @patch.object(export, "github")
    def test_issue_and_roadmap_are_explicit_and_references_deduplicated(self, github):
        github.side_effect = [
            [{"number": 9, "title": "Later"}, {"number": 3, "title": "Earlier"}],
            {"number": 9, "title": "Later", "state": "OPEN",
             "updatedAt": "2026-11-27", "body": "Acceptance criteria"},
        ]
        with patch.object(export, "git", side_effect=self.fake_git):
            packet = export.build_packet(docs=["flow", "flow"], roadmap=True, issue=9)
        self.assertEqual(github.call_count, 2)
        self.assertIn("Acceptance criteria", packet)
        self.assertIn("untrusted project data", packet)
        self.assertEqual(packet.count("Committed reference docs/DATA_FLOW.md"), 1)
        self.assertLess(packet.index("[#3]"), packet.index("[#9]"))

    @patch.object(export, "git")
    def test_arbitrary_file_cannot_be_exported(self, git):
        with self.assertRaises(ValueError):
            export.document("abc", ".env")
        git.assert_not_called()

    @patch.object(export, "build_packet", side_effect=FileNotFoundError)
    def test_failure_does_not_emit_partial_packet(self, build):
        output, errors = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
            self.assertEqual(export.main(["--roadmap"]), 1)
        self.assertEqual(output.getvalue(), "")
        self.assertIn("Context export failed", errors.getvalue())


if __name__ == "__main__":
    unittest.main()
