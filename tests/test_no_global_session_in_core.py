import unittest
import re
from pathlib import Path


class NoGlobalSessionInCoreTest(unittest.TestCase):
    def test_new_core_modules_do_not_import_global_session(self):
        root = Path(__file__).resolve().parents[1]
        files = [
            root / "services/payment_service.py",
            root / "services/booking_management_service.py",
            root / "services/stay_service.py",
            root / "services/booking_card_service.py",
            root / "services/booking_notification_service.py",
            root / "services/daily_summary_service.py",
            root / "services/error_monitor_service.py",
            root / "bot/routers/admin_payments.py",
            root / "bot/routers/admin_booking_edit.py",
            root / "bot/routers/my_bookings.py",
        ]
        for path in files:
            source = path.read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\bimport\s+session\b", source), path.name)
            self.assertIsNone(re.search(r"(?<![_A-Za-z])session\.query\(", source), path.name)


if __name__ == "__main__":
    unittest.main()
