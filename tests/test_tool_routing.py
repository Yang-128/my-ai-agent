import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import chat


class ToolRoutingTests(unittest.TestCase):

    def route(self, message, available, selected, history=None):
        definitions = [
            {"function": {
                "name": name,
                "description": name,
                "parameters": {}
            }}
            for name in available
        ]

        response = SimpleNamespace(choices=[
            SimpleNamespace(message=SimpleNamespace(
                content=json.dumps({
                    "needs_extensions": bool(selected),
                    "required_extensions": selected,
                    "reason": "Test routing"
                })
            ))
        ])

        with patch.object(chat, "get_provider_client") as client:
            create = client.return_value.chat.completions.create
            create.return_value = response

            result = chat.ai_decide_extensions(
                message, "groq", "test-model",
                definitions, history
            )

            if available:
                prompt = create.call_args.kwargs["messages"][0]["content"]
                self.assertIn("TOOL ROUTING RULES:", prompt)

            return result

    def test_conversational_request(self):
        result = self.route(
            "Explain photosynthesis.",
            ["mcp_web_search"], []
        )
        self.assertFalse(result["needs_extensions"])

    def test_web_dependencies(self):
        result = self.route(
            "Search the web for information.",
            ["mcp_web_search", "mcp_web_fetch"], []
        )
        self.assertEqual(set(result["required_extensions"]), {
            "mcp_web_search", "mcp_web_fetch"
        })

    def test_calendar_dependencies(self):
        names = [
            "mcp_get_current_time",
            "mcp_search_calendar_events",
            "mcp_get_calendar_event_details",
            "mcp_update_calendar_event"
        ]
        result = self.route(
            "Move my calendar event to tomorrow.",
            names, ["mcp_update_calendar_event"]
        )
        self.assertEqual(set(result["required_extensions"]), set(names))

    def test_unavailable_tools_rejected(self):
        result = self.route(
            "Hello",
            ["mcp_web_search"],
            ["mcp_nonexistent"]
        )
        self.assertFalse(result["needs_extensions"])

    def test_duplicate_tools_removed(self):
        result = self.route(
            "Research this topic.",
            ["mcp_web_search"],
            ["mcp_web_search", "mcp_web_search"]
        )
        self.assertEqual(
            result["required_extensions"],
            ["mcp_web_search"]
        )

    def test_gmail_read_does_not_trigger_calendar_or_send(self):
        result = self.route(
            "Do I have new emails today?",
            [
                "mcp_search_gmail_messages",
                "mcp_get_gmail_message",
                "mcp_send_gmail_message",
                "mcp_get_current_time",
                "mcp_search_calendar_events",
                "mcp_get_calendar_event_details",
                "mcp_search_google_tasks",
            ],
            ["mcp_search_gmail_messages", "mcp_get_current_time"]
        )
        self.assertEqual(
            set(result["required_extensions"]),
            {"mcp_search_gmail_messages", "mcp_get_current_time"}
        )

    def test_gmail_read_fallback(self):
        result = self.route(
            "Do I have any unread emails?",
            ["mcp_search_gmail_messages", "mcp_send_gmail_message"],
            []
        )
        self.assertEqual(
            result["required_extensions"],
            ["mcp_search_gmail_messages"]
        )

    def test_gmail_send_routing(self):
        result = self.route(
            "Send an email to my teacher.",
            ["mcp_search_gmail_messages", "mcp_send_gmail_message"],
            []
        )
        self.assertEqual(
            result["required_extensions"],
            ["mcp_send_gmail_message"]
        )

    def test_no_available_tools(self):
        result = self.route("Hello", [], [])
        self.assertFalse(result["needs_extensions"])


if __name__ == "__main__":
    unittest.main()
