import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from calendar_approval import create_pending_calendar_actions
from school_timetable import weekly_school_actions


PROMPT = """Put these events into my calendar: Art 10 (repeat: 859-1006 Tues and Thurs, 1052-1202 Mon and Wed, 1016-1127 Fri) Science 9(repeat:1252-157 Mon and Wed, 204-309 Tue and Thur, 1157-109 Fri) Design 11(repeat: 1252-157 Tue and Thur, 204-309 Mon and Wed, 114-227 Fri) School Band(Tuesdays and Thursdays 1224-1252) Studio Class(Tuesdays 1006-1052)"""
NOW = datetime(2026, 11, 22, 22, tzinfo=ZoneInfo("America/Vancouver"))


class TimetableTests(unittest.TestCase):
    def test_weekly_request_through_approval(self):
        actions = weekly_school_actions(PROMPT, NOW)
        with tempfile.TemporaryDirectory() as folder, patch(
            "calendar_approval.PENDING_FILE", Path(folder) / "pending.json"
        ):
            batch = create_pending_calendar_actions(actions, "test")
        actual = []
        for proposal in batch["proposals"]:
            event = proposal["event"]
            start = datetime.fromisoformat(event["start"]["dateTime"])
            end = datetime.fromisoformat(event["end"]["dateTime"])
            self.assertEqual(start.date(), end.date())
            actual.append((event["summary"], start.day, start.strftime("%H:%M"),
                           end.strftime("%H:%M"), event["recurrence"][0].split("=")[-1]))
        self.assertEqual(actual, [
            ("Art 10", 24, "08:59", "10:06", "TU,TH"),
            ("Art 10", 23, "10:52", "12:02", "MO,WE"),
            ("Art 10", 27, "10:16", "11:27", "FR"),
            ("Science 9", 23, "12:52", "13:57", "MO,WE"),
            ("Science 9", 24, "14:04", "15:09", "TU,TH"),
            ("Science 9", 27, "11:57", "13:09", "FR"),
            ("Design 11", 24, "12:52", "13:57", "TU,TH"),
            ("Design 11", 23, "14:04", "15:09", "MO,WE"),
            ("Design 11", 27, "13:14", "14:27", "FR"),
            ("School Band", 24, "12:24", "12:52", "TU,TH"),
            ("Studio Class", 24, "10:06", "10:52", "TU"),
        ])

    def test_preserves_extra_constraints_for_normal_chat_handling(self):
        self.assertIsNone(weekly_school_actions(PROMPT + " until January", NOW))

    def test_invalid_range(self):
        with self.assertRaisesRegex(ValueError, "Art 10"):
            weekly_school_actions(PROMPT.replace("859-1006", "959-859"), NOW)
