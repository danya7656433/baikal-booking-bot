import os
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("ADMIN_CHAT_ID", "1")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Booking, NotificationLog
from services.booking_notification_service import process_booking_notifications


class FakeBot:
    def __init__(self):
        self.messages = []

    async def send_message(self, chat_id, text, **kwargs):
        self.messages.append((chat_id, text, kwargs))


class NotificationWorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{(Path(self.temp_dir.name) / 'notifications.db').as_posix()}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session.begin() as db_session:
            db_session.add(Booking(
                user_id=77, username="guest", full_name="Guest", phone="1", comment="",
                date_from=date(2026, 6, 19), date_to=date(2026, 6, 21), room_type="4",
                adults=4, children=0, children_beds="[]", status="paid",
                calculated_total=12000, paid_amount=6000, refund_amount=0,
            ))

        @contextmanager
        def test_session():
            session = self.Session()
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise
            finally:
                session.close()
        self.patch = patch("services.booking_notification_service.get_session", test_session)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        self.engine.dispose()
        self.temp_dir.cleanup()

    async def test_reminders_include_balance_and_do_not_duplicate(self):
        bot = FakeBot()
        self.assertEqual(await process_booking_notifications(bot, date(2026, 6, 12)), 2)
        self.assertEqual(await process_booking_notifications(bot, date(2026, 6, 12)), 0)
        self.assertEqual(len(bot.messages), 2)
        self.assertTrue(all("Осталось оплатить: 6000₽" in text for _, text, _ in bot.messages))
        with self.Session() as db_session:
            self.assertEqual(db_session.query(NotificationLog).count(), 2)


if __name__ == "__main__":
    unittest.main()
