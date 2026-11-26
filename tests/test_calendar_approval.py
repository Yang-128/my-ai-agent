import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import calendar_approval as calendar


class CalendarApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.pending_file_patch = patch.object(
            calendar, "PENDING_FILE", Path(self.temp_dir.name) / "pending.json"
        )
        self.pending_file_patch.start()

    def tearDown(self):
        self.pending_file_patch.stop()
        self.temp_dir.cleanup()

    def create_all_day_proposal(self):
        return calendar.create_pending_calendar_action(
            {
                "summary": "Regression test",
                "start_date": "2026-12-01",
                "end_date": "2026-12-02",
            },
            "calendar-test",
            "create",
        )["pending_id"]

    def test_all_day_date_edit_replaces_both_endpoints(self):
        pending_id = self.create_all_day_proposal()
        calendar.update_pending_calendar_event_times(
            pending_id, start_date="2026-12-03", end_date="2026-12-05"
        )
        event = calendar.get_pending_calendar_event(pending_id)["proposal"]["event"]
        self.assertEqual(event["start"], {"date": "2026-12-03"})
        self.assertEqual(event["end"], {"date": "2026-12-05"})
        self.assertTrue(calendar.calendar_event_is_all_day(event))

    def test_all_day_event_can_be_converted_to_timed(self):
        pending_id = self.create_all_day_proposal()
        calendar.update_pending_calendar_event_times(
            pending_id,
            start_time="2026-12-03T09:00",
            end_time="2026-12-03T10:00",
        )
        event = calendar.get_pending_calendar_event(pending_id)["proposal"]["event"]
        self.assertIn("dateTime", event["start"])
        self.assertIn("dateTime", event["end"])
        self.assertNotIn("date", event["start"])
        self.assertNotIn("date", event["end"])
        self.assertFalse(calendar.calendar_event_is_all_day(event))

    def test_incomplete_all_day_edit_is_rejected_without_mutation(self):
        pending_id = self.create_all_day_proposal()
        with self.assertRaisesRegex(ValueError, "Both start and end dates"):
            calendar.update_pending_calendar_event_times(
                pending_id, start_date="2026-12-04"
            )
        event = calendar.get_pending_calendar_event(pending_id)["proposal"]["event"]
        self.assertEqual(event["start"], {"date": "2026-12-01"})
        self.assertEqual(event["end"], {"date": "2026-12-02"})

    def test_event_type_detection_rejects_mixed_or_incomplete_endpoints(self):
        with self.assertRaisesRegex(ValueError, "matching all-day dates"):
            calendar.calendar_event_is_all_day({
                "start": {"date": "2026-12-01"},
                "end": {"dateTime": "2026-12-02T10:00:00-08:00"},
            })
        with self.assertRaises(ValueError):
            calendar.calendar_event_is_all_day({
                "start": {"date": "2026-12-01"},
                "end": {},
            })

    def test_event_type_detection_accepts_consistent_date_and_datetime_pairs(self):
        self.assertTrue(calendar.calendar_event_is_all_day({
            "start": {"date": "2026-12-01"},
            "end": {"date": "2026-12-02"},
        }))
        self.assertFalse(calendar.calendar_event_is_all_day({
            "start": {"dateTime": "2026-12-01T09:00:00-08:00"},
            "end": {"dateTime": "2026-12-01T10:00:00-08:00"},
        }))


if __name__ == "__main__":
    unittest.main()
