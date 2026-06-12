import os
import unittest
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

os.environ.setdefault("ADMIN_CHAT_ID", "1")

from services.stay_service import calculate_due_stay_status


class StayServiceTest(unittest.TestCase):
    def setUp(self):
        self.booking = SimpleNamespace(
            date_from=date(2026, 6, 22),
            date_to=date(2026, 6, 25),
            stay_status="awaiting_checkin",
        )
        self.tz = ZoneInfo("Asia/Irkutsk")

    def test_automatic_stay_status_boundaries(self):
        self.assertEqual(calculate_due_stay_status(self.booking, datetime(2026, 6, 22, 13, 59, tzinfo=self.tz)), "awaiting_checkin")
        self.assertEqual(calculate_due_stay_status(self.booking, datetime(2026, 6, 22, 14, 0, tzinfo=self.tz)), "checked_in")
        self.assertEqual(calculate_due_stay_status(self.booking, datetime(2026, 6, 25, 12, 0, tzinfo=self.tz)), "checked_out")

    def test_completed_is_never_downgraded(self):
        self.booking.stay_status = "completed"
        self.assertEqual(calculate_due_stay_status(self.booking, datetime(2026, 6, 25, 13, 0, tzinfo=self.tz)), "completed")


if __name__ == "__main__":
    unittest.main()
