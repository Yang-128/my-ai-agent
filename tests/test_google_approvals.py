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

    def test_task_http_approval_preserves_list_target_and_partial_edits(self):
        from fastapi.testclient import TestClient
        from task_approval import prepare_google_task_proposal

        endpoint = "/api/google/tasks/approve"
        with TestClient(app.app) as client, patch(
            "google_integrations.get_tasks_service"
        ) as service, patch("google_integrations.get_google_credentials") as credentials, patch(
            "google_integrations.build"
        ) as build:
            lookup = service.return_value
            lookup.tasklists().get().execute.return_value = {"id": "list-2", "title": "Study"}
            lookup.tasks().get().execute.return_value = {"id": "task-3", "title": "Review"}
            tasks = build.return_value.tasks.return_value
            cases = [
                ({"action": "create", "changes": {"title": "Review"}},
                 "insert", {"tasklist": "list-2", "body": {"title": "Review"}}),
                ({"action": "update", "task_id": "task-3", "changes": {"due": None, "status": "completed"}},
                 "patch", {"tasklist": "list-2", "task": "task-3", "body": {"due": None, "status": "completed"}}),
                ({"action": "delete", "task_id": "task-3"},
                 "delete", {"tasklist": "list-2", "task": "task-3"}),
            ]
            for proposal, operation, expected in cases:
                with self.subTest(operation=operation):
                    build.reset_mock()
                    pending = prepare_google_task_proposal({"task_list_id": "list-2", **proposal}, "chat")
                    build.assert_not_called()
                    decision = {"pending_id": pending["pending_id"], "approved": True}
                    response = client.post(endpoint, json=decision)
                    self.assertEqual(response.json(), {"status": "completed", "action": proposal["action"]})
                    getattr(tasks, operation).assert_called_once_with(**expected)
                    self.assertEqual(client.post(endpoint, json=decision).status_code, 409)
                    getattr(tasks, operation).assert_called_once()
            credentials.assert_called_with(require_tasks_write=True)

    def test_declined_and_expired_task_proposals_never_write(self):
        from fastapi.testclient import TestClient

        endpoint = "/api/google/tasks/approve"
        proposal = {"action": "delete", "task_list_id": "list-2", "task_id": "task-3"}
        with TestClient(app.app) as client, patch("google_integrations.execute_approved_google_task") as execute:
            pending = save_task_proposal(proposal, "chat")
            decision = {"pending_id": pending["pending_id"], "approved": False}
            self.assertEqual(client.post(endpoint, json=decision).json(), {"status": "declined"})
            self.assertEqual(client.post(endpoint, json={**decision, "approved": True}).status_code, 409)
            with patch("task_approval.EXPIRY_SECONDS", -1):
                expired = save_task_proposal(proposal, "chat")
            self.assertEqual(client.post(endpoint, json={
                "pending_id": expired["pending_id"], "approved": True,
            }).status_code, 409)
            execute.assert_not_called()
