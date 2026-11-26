import unittest
from unittest.mock import patch

from fastapi import HTTPException

import app
from calendar_approval import create_pending_calendar_action
from task_approval import save_task_proposal


class GoogleApprovalTests(unittest.TestCase):
    def test_title_only_calendar_update(self):
        pending = create_pending_calendar_action(
            {"event_id": "event", "summary": "New title"}, "chat", "update"
        )
        original = {"start": {"date": "2026-12-01"}, "end": {"date": "2026-12-02"}}
        with patch.object(app, "get_calendar_event", return_value=original), patch.object(
            app, "update_calendar_event", return_value={"id": "event"}
        ) as update:
            app.google_calendar_approve_event({"pending_id": pending["pending_id"], "approved": True, "times": [{}]})
        update.assert_called_once_with("event", {"summary": "New title"})

    def test_task_approval_can_only_execute_once(self):
        pending = save_task_proposal({"action": "create", "task_list_id": "list",
                                      "changes": {"title": "Example"}}, "chat")
        decision = {"pending_id": pending["pending_id"], "approved": True}
        with patch("google_integrations.execute_approved_google_task") as execute:
            self.assertEqual(app.google_tasks_approve(decision)["status"], "completed")
            with self.assertRaises(HTTPException) as replay:
                app.google_tasks_approve(decision)
            self.assertEqual(replay.exception.status_code, 409)
            execute.assert_called_once()

    def test_private_google_files_are_excluded_from_project_browser(self):
        for name in ("google_calendar_token_test.json", "google_calendar_signin_test.processing",
                     "google_oauth_state.json", "pending_google_tasks.sqlite3-journal"):
            with self.subTest(name=name), self.assertRaises(HTTPException) as denied:
                app.resolve_browser_path(name)
            self.assertEqual(denied.exception.status_code, 403)
