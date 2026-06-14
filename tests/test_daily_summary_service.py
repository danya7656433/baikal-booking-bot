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
    def test_summary_contains_detailed_card_and_excludes_rejected_booking(self):
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
                db_session.add(Booking(
                    user_id=2, username="old", full_name="Не показывать", phone="2", comment="",
                    date_from=date(2026, 6, 12), date_to=date(2026, 6, 13), room_type="4",
                    adults=2, children=0, children_beds="[]", status="rejected",
                    calculated_total=6000, paid_amount=0, refund_amount=0,
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
        self.assertIn("Гость: Даниил (@guest)", text)
        self.assertIn("Телефон: 1", text)
        self.assertIn("Размещение:", text)
        self.assertIn("Стоимость: 12000₽", text)
        self.assertIn("Осталось: 7000₽", text)
        self.assertIn("Итого:", text)
        self.assertNotIn("Не показывать", text)

    def test_summary_marks_suspicious_guest_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            engine = create_engine(f"sqlite:///{(Path(temp_dir) / 'summary.db').as_posix()}")
            Base.metadata.create_all(engine)
            Session = sessionmaker(bind=engine)
            with Session.begin() as db_session:
                db_session.add(Booking(
                    user_id=1, username=None, full_name="🫠", phone="1", comment="",
                    date_from=date(2026, 6, 12), date_to=date(2026, 6, 13), room_type="4",
                    adults=2, children=0, children_beds="[]", status="paid",
                    calculated_total=6000, paid_amount=6000, refund_amount=0,
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
        self.assertIn("Требуют внимания", text)
        self.assertIn("некорректное имя", text)


if __name__ == "__main__":
    unittest.main()
