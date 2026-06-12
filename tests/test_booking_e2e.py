import os
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

os.environ.setdefault("ADMIN_CHAT_ID", "1")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Booking, BookingChange, PaymentTransaction
from services.booking_management_service import update_booking_accommodation
from services.payment_service import add_payment
from services.stay_service import apply_due_stay_transitions
from tests.helpers import session_context


class BookingLifecycleE2ETest(unittest.IsolatedAsyncioTestCase):
    async def test_full_financial_and_operational_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_engine(f"sqlite:///{(Path(temp_dir) / 'e2e.db').as_posix()}")
            Base.metadata.create_all(engine)
            Session = sessionmaker(bind=engine)
            with Session.begin() as db_session:
                booking = Booking(
                    user_id=1, username="guest", full_name="Guest", phone="1", comment="",
                    date_from=date(2026, 6, 20), date_to=date(2026, 6, 22), room_type="4",
                    adults=4, children=0, children_beds="[]", status="paid",
                    calculated_total=12000, paid_amount=0, refund_amount=0,
                )
                db_session.add(booking)
                db_session.flush()
                booking_id = booking.id
            test_session = session_context(Session)
            with patch("services.payment_service.get_session", test_session), \
                 patch("services.booking_management_service.get_session", test_session), \
                 patch("services.stay_service.get_session", test_session), \
                 patch("services.booking_management_service.calculate_revenue", new=AsyncMock(return_value=18000)):
                self.assertEqual(add_payment(booking_id, 6000, "перевод", 99)["payment_status"], "partially_paid")
                self.assertEqual(add_payment(booking_id, 6000, "перевод", 99)["payment_status"], "paid")
                changed = await update_booking_accommodation(
                    booking_id, 99, date_from=date(2026, 7, 1), date_to=date(2026, 7, 4), room_type="6"
                )
                self.assertEqual(changed["paid"], 12000)
                self.assertEqual(changed["remaining"], 6000)
                apply_due_stay_transitions(datetime(2026, 7, 1, 14, 0, tzinfo=ZoneInfo("Asia/Irkutsk")))
                apply_due_stay_transitions(datetime(2026, 7, 4, 12, 0, tzinfo=ZoneInfo("Asia/Irkutsk")))
            with Session() as db_session:
                booking = db_session.get(Booking, booking_id)
                self.assertEqual(booking.stay_status, "checked_out")
                self.assertEqual(db_session.query(PaymentTransaction).count(), 2)
                self.assertGreaterEqual(db_session.query(BookingChange).count(), 3)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
