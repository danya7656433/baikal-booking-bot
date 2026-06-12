import os
import unittest

os.environ.setdefault("ADMIN_CHAT_ID", "1")

from services.error_monitor_service import make_error_fingerprint


class ErrorMonitorServiceTest(unittest.TestCase):
    def test_same_error_has_same_fingerprint(self):
        first = make_error_fingerprint(ValueError("bad"), "line 1\nline 2")
        second = make_error_fingerprint(ValueError("bad"), "line 1\nline 2")
        different = make_error_fingerprint(ValueError("other"), "line 1\nline 2")
        self.assertEqual(first, second)
        self.assertNotEqual(first, different)


if __name__ == "__main__":
    unittest.main()
