import os
import unittest

os.environ.setdefault("ADMIN_CHAT_ID", "1")

from services.booking_card_service import format_admin_booking_card, format_guest_booking_card


class BookingCardServiceTest(unittest.TestCase):
    def setUp(self):
        self.data = {
            "id": 16, "room": "2+4", "date_from": "22.06.2026", "date_to": "25.06.2026",
            "people": 5, "adults": 5, "children": 0, "children_beds": 0,
            "total": 22500, "paid": 10000, "refunds": 0, "remaining": 12500,
            "required_prepayment": 11250, "payment_status": "partially_paid",
            "booking_status": "paid", "stay_status": "awaiting_checkin",
            "comment": "Без комментария", "admin_comment": "нет",
            "full_name": "Даниил", "username": "redtexas", "phone": "79000000000",
        }

    def test_guest_and_admin_cards_show_same_finances(self):
        guest = format_guest_booking_card(self.data)
        admin = format_admin_booking_card(self.data)
        for text in (guest, admin):
            self.assertIn("Внесено: 10000₽", text)
            self.assertIn("Осталось: 12500₽", text)
            self.assertIn("🟠 Оплачено частично", text)
        self.assertNotIn("Админ-комментарий", guest)
        self.assertIn("Админ-комментарий", admin)


if __name__ == "__main__":
    unittest.main()
