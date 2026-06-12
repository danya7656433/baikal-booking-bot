import tempfile
import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from db.models import Base, Booking, PaymentTransaction
from services.payment_service import (
    add_adjustment,
    add_payment,
    add_refund,
    get_payment_balance,
    preview_payment,
)


class PaymentServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "payments.db"
        self.engine = create_engine(f"sqlite:///{database_path.as_posix()}")
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)

        with self.Session.begin() as db_session:
            booking = Booking(
                user_id=10,
                username="guest",
                full_name="Test Guest",
                phone="79000000000",
                comment="",
                date_from=date(2026, 6, 22),
                date_to=date(2026, 6, 24),
                room_type="6",
                adults=6,
                children=0,
                manual_total=10000,
                calculated_total=10000,
                paid_amount=0,
                refund_amount=0,
            )
            db_session.add(booking)
            db_session.flush()
            self.booking_id = booking.id

        @contextmanager
        def test_get_session():
            db_session = self.Session()
            try:
                yield db_session
                db_session.commit()
            except Exception:
                db_session.rollback()
                raise
            finally:
                db_session.close()

        self.session_patch = patch(
            "services.payment_service.get_session", test_get_session
        )
        self.session_patch.start()

    def tearDown(self):
        self.session_patch.stop()
        self.engine.dispose()
        self.temp_dir.cleanup()

    def read_booking(self):
        with self.Session() as db_session:
            return db_session.get(Booking, self.booking_id)

    def read_transactions(self):
        with self.Session() as db_session:
            return list(
                db_session.scalars(
                    select(PaymentTransaction)
                    .where(PaymentTransaction.booking_id == self.booking_id)
                    .order_by(PaymentTransaction.id)
                )
            )

    def test_payments_and_refund_create_history_and_recalculate_balance(self):
        add_payment(self.booking_id, 6000, "перевод", 101)
        add_payment(self.booking_id, 4000, "наличные", 101)
        balance = add_refund(self.booking_id, 2000, "возврат", 101, "ошибка")

        self.assertEqual(balance["paid"], 10000)
        self.assertEqual(balance["refunds"], 2000)
        self.assertEqual(balance["net_paid"], 8000)
        self.assertEqual(balance["remaining"], 2000)
        self.assertEqual(balance["payment_status"], "partially_paid")

        booking = self.read_booking()
        self.assertEqual(booking.paid_amount, 10000)
        self.assertEqual(booking.refund_amount, 2000)
        self.assertEqual(booking.payment_status, "partially_paid")
        self.assertEqual(
            [transaction.kind for transaction in self.read_transactions()],
            ["payment", "payment", "refund"],
        )

    def test_payment_statuses_cover_partial_paid_overpaid_and_refunded(self):
        self.assertEqual(
            add_payment(self.booking_id, 4000, "перевод", 101)["payment_status"],
            "partially_paid",
        )
        self.assertEqual(
            add_payment(self.booking_id, 6000, "перевод", 101)["payment_status"],
            "paid",
        )
        self.assertEqual(
            add_payment(self.booking_id, 1000, "перевод", 101)["payment_status"],
            "overpaid",
        )
        self.assertEqual(
            add_refund(self.booking_id, 11000, "возврат", 101, "полный возврат")[
                "payment_status"
            ],
            "refunded",
        )

    def test_adjustment_requires_comment_and_changes_compatible_cache(self):
        add_payment(self.booking_id, 6000, "перевод", 101)

        with self.assertRaisesRegex(ValueError, "comment"):
            add_adjustment(self.booking_id, 1000, 101, "")

        balance = add_adjustment(self.booking_id, -1000, 101, "исправление")
        self.assertEqual(balance["paid"], 5000)
        self.assertEqual(balance["net_paid"], 5000)
        self.assertEqual(self.read_booking().paid_amount, 5000)
        self.assertEqual(self.read_transactions()[-1].kind, "adjustment")

    def test_invalid_operations_are_rolled_back_atomically(self):
        with self.assertRaises(ValueError):
            add_payment(self.booking_id, -1, "перевод", 101)
        with self.assertRaises(ValueError):
            add_refund(self.booking_id, 1, "возврат", 101, "нет оплаты")

        self.assertEqual(self.read_transactions(), [])
        booking = self.read_booking()
        self.assertEqual(booking.paid_amount, 0)
        self.assertEqual(booking.refund_amount, 0)

    def test_preview_does_not_persist_and_missing_booking_is_rejected(self):
        with self.Session() as db_session:
            preview = preview_payment(db_session, self.booking_id, 12000)
            persisted = get_payment_balance(db_session, self.booking_id)

        self.assertEqual(preview["payment_status"], "overpaid")
        self.assertEqual(preview["overpaid"], 2000)
        self.assertEqual(persisted["net_paid"], 0)
        self.assertEqual(self.read_transactions(), [])

        with self.assertRaises(LookupError):
            add_payment(999999, 1000, "перевод", 101)

    def test_balance_includes_discount_and_extra_services(self):
        with self.Session.begin() as db_session:
            booking = db_session.get(Booking, self.booking_id)
            booking.discount_amount = 1000
            booking.extra_services_amount = 2500
        with self.Session() as db_session:
            balance = get_payment_balance(db_session, self.booking_id)
        self.assertEqual(balance["total"], 11500)

    def test_first_new_payment_preserves_legacy_paid_amount(self):
        with self.Session.begin() as db_session:
            booking = db_session.get(Booking, self.booking_id)
            booking.paid_amount = 3000
            booking.refund_amount = 500

        balance = add_payment(self.booking_id, 2000, "перевод", 101)

        self.assertEqual(balance["paid"], 5000)
        self.assertEqual(balance["refunds"], 500)
        self.assertEqual(balance["net_paid"], 4500)
        self.assertEqual(
            [transaction.kind for transaction in self.read_transactions()],
            ["adjustment", "refund", "payment"],
        )


if __name__ == "__main__":
    unittest.main()
