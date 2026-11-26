import unittest

from tool_loading import (
    EXPAND_TOOL_NAME,
    select_initial_tools,
    expand_tool_groups,
    tool_name,
)


class ToolLoadingTests(unittest.TestCase):

    def setUp(self):
        self.registry = [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": name,
                    "parameters": {},
                },
            }
            for name in (
                "mcp_search_gmail_messages",
                "mcp_get_gmail_message",
                "mcp_get_gmail_thread",
                "mcp_send_gmail_message",
                "mcp_search_calendar_events",
                "mcp_create_calendar_events",
                "mcp_search_google_tasks",
                "mcp_propose_google_task_change",
                "mcp_web_search",
                "mcp_web_fetch",
                "mcp_mac_search_files",
                "mcp_mac_read_file",
                "mcp_mac_create_document",
                "mcp_search_conversations",
                "mcp_get_current_time",
            )
        ]

    def names(self, tools):
        return {tool_name(tool) for tool in tools}

    def test_general_question_has_no_tools(self):
        tools, mode = select_initial_tools(
            "Explain quadratic equations.", self.registry
        )
        self.assertEqual(tools, [])
        self.assertEqual(mode, "general")

    def test_email_read_does_not_load_send(self):
        tools, mode = select_initial_tools(
            "Check my emails.", self.registry
        )
        self.assertEqual(mode, "scoped")
        self.assertEqual(self.names(tools), {
            "mcp_search_gmail_messages",
            "mcp_get_gmail_message",
            "mcp_get_gmail_thread",
            EXPAND_TOOL_NAME,
        })

    def test_email_send_loads_proposal_tool(self):
        tools, _ = select_initial_tools(
            "Send an email to my teacher.", self.registry
        )
        self.assertIn(
            "mcp_send_gmail_message", self.names(tools)
        )

    def test_web_search_is_scoped(self):
        tools, _ = select_initial_tools(
            "Search the web for Python updates.", self.registry
        )
        self.assertEqual(self.names(tools), {
            "mcp_web_search",
            "mcp_web_fetch",
            EXPAND_TOOL_NAME,
        })

    def test_multi_service_request(self):
        tools, _ = select_initial_tools(
            "Organize my calendar and Google Tasks.",
            self.registry,
        )
        names = self.names(tools)
        self.assertIn("mcp_search_calendar_events", names)
        self.assertIn("mcp_search_google_tasks", names)
        self.assertNotIn("mcp_search_gmail_messages", names)

    def test_ambiguous_followup_has_full_access(self):
        tools, mode = select_initial_tools(
            "Do it.", self.registry
        )
        self.assertEqual(tools, self.registry)
        self.assertEqual(mode, "full")

    def test_dynamic_expansion(self):
        tools, _ = select_initial_tools(
            "Check my emails.", self.registry
        )
        expanded = expand_tool_groups(
            tools, self.registry, ["calendar"]
        )
        names = self.names(expanded)
        self.assertIn("mcp_search_gmail_messages", names)
        self.assertIn("mcp_search_calendar_events", names)
        self.assertIn(EXPAND_TOOL_NAME, names)

    def test_full_expansion_removes_virtual_tool(self):
        tools, _ = select_initial_tools(
            "Check my emails.", self.registry
        )
        expanded = expand_tool_groups(
            tools, self.registry, ["all"]
        )
        self.assertEqual(expanded, self.registry)
        self.assertNotIn(EXPAND_TOOL_NAME, self.names(expanded))

    def test_digest_remains_read_only(self):
        tools, mode = select_initial_tools(
            "Do I have new emails?",
            self.registry,
            daily_digest=True,
        )
        self.assertEqual(mode, "digest")
        self.assertEqual(self.names(tools), {
            "mcp_search_gmail_messages",
            "mcp_get_gmail_message",
            "mcp_get_gmail_thread",
        })

    def test_image_request_remains_unrestricted(self):
        tools, mode = select_initial_tools(
            "Explain this.",
            self.registry,
            has_images=True,
        )
        self.assertEqual(mode, "full")
        self.assertEqual(tools, self.registry)

    def test_external_information_not_treated_as_general(self):
        tools, mode = select_initial_tools(
            "Explain what happened today.", self.registry
        )
        self.assertEqual(mode, "full")

    def test_invalid_group_rejected(self):
        with self.assertRaises(ValueError):
            expand_tool_groups(
                [], self.registry, ["nonexistent"]
            )
