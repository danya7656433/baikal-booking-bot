import tempfile
import unittest
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.models import Base, Booking
from services.booking_management_service import get_admin_booking_counts


class AdminBookingCountsTest(unittest.TestCase):
    def test_deleted_bookings_are_not_counted_in_admin_room_buttons(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_dir:
            engine = create_engine(
                f"sqlite:///{(Path(temp_dir) / 'admin_counts.db').as_posix()}"
            )
            Base.metadata.create_all(engine)
            Session = sessionmaker(bind=engine)
            with Session.begin() as db_session:
                db_session.add_all(
                    [
                        Booking(
                            room_type="4",
                            status="paid",
                            date_from=date(2026, 6, 20),
                            date_to=date(2026, 6, 22),
                        ),
                        Booking(
                            room_type="4",
                            status="paid",
                            date_from=date(2026, 6, 23),
                            date_to=date(2026, 6, 25),
                            deleted_at=datetime(2026, 6, 19, 10, 0),
                        ),
                        Booking(
                            room_type="2",
                            status="cancelled",
                            date_from=date(2026, 6, 23),
                            date_to=date(2026, 6, 25),
                        ),
                    ]
                )

            @contextmanager
            def test_session():
                db_session = Session()
                try:
                    yield db_session
                    db_session.commit()
                finally:
                    db_session.close()

            try:
                with patch("services.booking_management_service.get_session", test_session):
                    counts = get_admin_booking_counts()
            finally:
                engine.dispose()

        self.assertEqual(counts["room_counts"]["4"], 1)
        self.assertEqual(counts["room_counts"]["2"], 0)
        self.assertEqual(counts["awaiting_payment_count"], 0)


if __name__ == "__main__":
    unittest.main()
