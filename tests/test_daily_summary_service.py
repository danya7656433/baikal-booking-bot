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

from db.models import Base, Booking
from services.daily_summary_service import build_daily_summary


class DailySummaryServiceTest(unittest.TestCase):
    def test_summary_contains_arrival_departure_and_balance(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_engine(f"sqlite:///{(Path(temp_dir) / 'summary.db').as_posix()}")
            Base.metadata.create_all(engine)
            Session = sessionmaker(bind=engine)
            with Session.begin() as db_session:
                db_session.add(Booking(
                    user_id=1, username="guest", full_name="Даниил", phone="1", comment="",
                    date_from=date(2026, 6, 12), date_to=date(2026, 6, 12), room_type="4",
                    adults=4, children=0, children_beds="[]", status="paid",
                    calculated_total=12000, paid_amount=5000, refund_amount=0,
                ))

            @contextmanager
            def test_session():
                session = Session()
                try:
                    yield session
                    session.commit()
                finally:
                    session.close()

            with patch("services.daily_summary_service.get_session", test_session):
                text = build_daily_summary(date(2026, 6, 12))
            engine.dispose()
        self.assertIn("Заезды", text)
        self.assertIn("Выезды", text)
        self.assertIn("Остатки к оплате", text)
        self.assertIn("7000₽", text)


if __name__ == "__main__":
    unittest.main()
