import os
import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ADMIN_CHAT_ID", "1")

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from db.models import Base, Booking, BookingChange
from services.booking_management_service import update_booking_accommodation


class BookingManagementServiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        path = Path(self.temp_dir.name) / "management.db"
        self.engine = create_engine(f"sqlite:///{path.as_posix()}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.Session.begin() as db_session:
            booking = Booking(
                user_id=1, username="guest", full_name="Guest", phone="1", comment="",
                date_from=date(2026, 6, 20), date_to=date(2026, 6, 22), room_type="4",
                adults=4, children=0, children_beds="[]", manual_total=12000,
                calculated_total=12000, paid_amount=5000, refund_amount=0,
            )
            db_session.add(booking)
            db_session.flush()
            self.booking_id = booking.id

        @contextmanager
        def test_session():
            db_session = self.Session()
            try:
                yield db_session
                db_session.commit()
            except Exception:
                db_session.rollback()
                raise
            finally:
                db_session.close()

        self.session_patch = patch("services.booking_management_service.get_session", test_session)
        self.price_patch = patch("services.booking_management_service.calculate_revenue", new=AsyncMock(return_value=18000))
        self.session_patch.start()
        self.price_patch.start()

    async def asyncTearDown(self):
        self.price_patch.stop()
        self.session_patch.stop()
        self.engine.dispose()
        self.temp_dir.cleanup()

    async def test_change_recalculates_total_preserves_payment_and_writes_history(self):
        result = await update_booking_accommodation(
            self.booking_id, 99,
            date_from=date(2026, 7, 1), date_to=date(2026, 7, 4), room_type="6",
        )
        self.assertEqual(result["total"], 18000)
        self.assertEqual(result["paid"], 5000)
        self.assertEqual(result["remaining"], 13000)
        with self.Session() as db_session:
            booking = db_session.get(Booking, self.booking_id)
            changes = list(db_session.scalars(select(BookingChange)))
        self.assertEqual(booking.room_type, "6")
        self.assertEqual(booking.paid_amount, 5000)
        self.assertEqual(len(changes), 1)

    async def test_invalid_dates_are_rejected(self):
        with self.assertRaises(ValueError):
            await update_booking_accommodation(
                self.booking_id, 99,
                date_from=date(2026, 7, 4), date_to=date(2026, 7, 4),
            )


if __name__ == "__main__":
    unittest.main()
