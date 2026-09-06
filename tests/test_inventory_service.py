import unittest
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import Base, Booking, BlockedDate
from db.inventory_guards import install_inventory_guards
from services.inventory_service import is_available, rooms_overlap


class InventoryTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        with self.engine.begin() as connection:
            install_inventory_guards(connection)
        self.session = Session(self.engine)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def booking(self, room="2", status="new", start=10, end=12):
        booking = Booking(room_type=room, status=status, date_from=date(2027, 7, start), date_to=date(2027, 7, end))
        self.session.add(booking)
        self.session.commit()
        return booking

    def test_disjoint_rooms_do_not_conflict(self):
        self.booking()
        self.assertFalse(rooms_overlap("2", "4"))
        self.assertTrue(is_available(self.session, "4", date(2027, 7, 10), date(2027, 7, 12)))

    def test_payment_review_and_cancellation_hold_inventory(self):
        for status in ("awaiting_payment_confirmation", "awaiting_cancellation"):
            booking = self.booking(status=status)
            self.assertFalse(is_available(self.session, "2+6", date(2027, 7, 10), date(2027, 7, 11)))
            self.session.delete(booking)
            self.session.commit()

    def test_checkout_day_is_available_and_combo_is_blocked(self):
        self.booking("2+4")
        self.assertTrue(is_available(self.session, "2", date(2027, 7, 12), date(2027, 7, 14)))
        self.assertFalse(is_available(self.session, "4+6", date(2027, 7, 11), date(2027, 7, 13)))
        self.assertFalse(is_available(self.session, "all", date(2027, 7, 11), date(2027, 7, 13)))

    def test_database_rejects_overlap_even_without_application_check(self):
        self.booking("2+4")
        with self.assertRaises(IntegrityError):
            self.booking("4+6")
        self.session.rollback()
        self.assertEqual(self.session.query(Booking).count(), 1)

    def test_reactivation_cannot_overbook(self):
        old = self.booking(status="cancelled")
        self.booking()
        old.status = "paid"
        with self.assertRaises(IntegrityError):
            self.session.commit()
        self.session.rollback()

    def test_blocked_dates_protect_related_rooms(self):
        self.session.add(BlockedDate(room_type="5", date=date(2027, 7, 11)))
        self.session.commit()
        self.assertFalse(is_available(self.session, "5+6", date(2027, 7, 10), date(2027, 7, 12)))
        with self.assertRaises(IntegrityError):
            self.booking("5+6")
        self.session.rollback()
