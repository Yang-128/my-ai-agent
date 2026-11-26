import unittest

from gmail_digest import (
    is_daily_digest,
    priority_message,
    collect_daily_digest,
    ensure_digest_coverage,
)


class GmailDigestTests(unittest.TestCase):

    def test_generic_inbox_request(self):
        self.assertTrue(is_daily_digest("Do I have new emails?"))
        self.assertTrue(is_daily_digest("Do I have new emails today?"))
        self.assertFalse(is_daily_digest("Show me all emails from Canva"))

    def test_volunteer_schedule_is_priority(self):
        message = {
            "subject": "December volunteer schedule for VCM",
            "categories": ["promotions"],
            "unread": False,
        }
        self.assertTrue(priority_message(message))

    def test_unread_ad_is_not_priority(self):
        self.assertFalse(priority_message({
            "subject": "Black Friday offers ending soon",
            "unread": True,
        }))

    def test_collects_later_pages_and_restores_omitted_email(self):
        pages = {
            "": {
                "messages": [{
                    "id": "1",
                    "subject": "Black Friday sale",
                }],
                "next_page_token": "page2",
            },
            "page2": {
                "messages": [{
                    "id": "2",
                    "subject": "December volunteer schedule for VCM",
                    "sender": "Museum",
                }],
            },
        }

        digest = collect_daily_digest(
            lambda args: pages[args["page_token"]]
        )

        self.assertTrue(digest["complete"])
        self.assertEqual(digest["reviewed_count"], 2)

        reply = ensure_digest_coverage(
            "No important updates.", digest
        )

        self.assertIn(
            "December volunteer schedule for VCM", reply
        )
        self.assertNotIn("Black Friday sale", reply)

    def test_does_not_duplicate_covered_subject(self):
        digest = {
            "messages": [{
                "subject": "Volunteer schedule",
                "priority_candidate": True,
            }],
            "complete": True,
            "reviewed_count": 1,
        }

        reply = "Volunteer schedule"
        self.assertEqual(
            ensure_digest_coverage(reply, digest), reply
        )

    def test_incomplete_scan_is_disclosed(self):
        digest = collect_daily_digest(
            lambda args: {
                "messages": [],
                "next_page_token": "next",
            },
            max_pages=1,
        )

        self.assertFalse(digest["complete"])
        self.assertIn(
            "incomplete",
            ensure_digest_coverage("No updates.", digest)
        )
