import os
import unittest

os.environ.setdefault("ADMIN_CHAT_ID", "1")

from services.error_monitor_service import classify_error, make_error_fingerprint


class ErrorMonitorServiceTest(unittest.TestCase):
    def test_same_error_has_same_fingerprint(self):
        first = make_error_fingerprint(ValueError("bad"), "line 1\nline 2")
        second = make_error_fingerprint(ValueError("bad"), "line 1\nline 2")
        different = make_error_fingerprint(ValueError("other"), "line 1\nline 2")
        self.assertEqual(first, second)
        self.assertNotEqual(first, different)

    def test_transient_telegram_errors_are_not_critical(self):
        for message in (
            "Failed to fetch updates - TelegramRetryAfter: Flood control exceeded",
            "Failed to fetch updates - TelegramServerError: Bad Gateway",
            "Failed to fetch updates - TelegramNetworkError: Connection reset by peer",
        ):
            self.assertEqual(classify_error(RuntimeError(message)), "transient_telegram")

    def test_get_updates_conflict_is_critical(self):
        error = RuntimeError("Conflict: terminated by other getUpdates request")
        self.assertEqual(classify_error(error), "instance_conflict")

    def test_fingerprint_normalizes_retry_details(self):
        first = make_error_fingerprint(RuntimeError("Retry in 5 seconds"), "")
        second = make_error_fingerprint(RuntimeError("Retry in 9 seconds"), "")
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
