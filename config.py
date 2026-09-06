import os
from enum import Enum
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_CHAT_ID = int(os.getenv("ADMIN_CHAT_ID") or "0")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
_photo_storage_chat = os.getenv("PHOTO_STORAGE_CHAT_ID", "7162508527").strip()
PHOTO_STORAGE_CHAT_ID = (
    int(_photo_storage_chat)
    if _photo_storage_chat.lstrip("-").isdigit()
    else _photo_storage_chat
)
PRICE_PER_ADULT = 1500
ALL_ROOMS_PRICE = 18000
HOTEL_ADDRESS = "село Максимиха, ул. Дачная, 12"
ADMIN_CONTACT = "+7 (908) 594-89-48, Даниил"
LOCATION_LINK = "https://go.2gis.com/6f9MU"
PAYMENT_DEADLINE_HOURS = 3


class BookingStatus(str, Enum):
    NEW = "new"
    PENDING = "pending"
    AWAITING_PAYMENT = "awaiting_payment"
    AWAITING_PAYMENT_CONFIRMATION = "awaiting_payment_confirmation"
    PAID = "paid"
    COMPLETED = "completed"
    AWAITING_CANCELLATION = "awaiting_cancellation"
    CANCELLED = "cancelled"
    CANCELED = "canceled"
    REJECTED = "rejected"


ACTIVE_BOOKING_STATUSES = (
    BookingStatus.NEW.value,
    BookingStatus.PENDING.value,
    BookingStatus.AWAITING_PAYMENT.value,
    BookingStatus.AWAITING_PAYMENT_CONFIRMATION.value,
    BookingStatus.PAID.value,
    BookingStatus.AWAITING_CANCELLATION.value,
)

CANCELLED_BOOKING_STATUSES = (
    BookingStatus.CANCELLED.value,
    BookingStatus.CANCELED.value,
)

BOOKING_STATUS_LABELS = {
    BookingStatus.NEW.value: "Новая",
    BookingStatus.PENDING.value: "В обработке",
    BookingStatus.AWAITING_PAYMENT.value: "Ожидает оплату",
    BookingStatus.AWAITING_PAYMENT_CONFIRMATION.value: "Оплата на проверке",
    BookingStatus.PAID.value: "Оплачено, ждём в гости",
    BookingStatus.COMPLETED.value: "Завершена",
    BookingStatus.AWAITING_CANCELLATION.value: "Ожидает отмены",
    BookingStatus.CANCELLED.value: "Отменена",
    BookingStatus.CANCELED.value: "Отменена",
    BookingStatus.REJECTED.value: "Отклонена",
}

room_type_names = {
    "2": "2х местный номер",
    "5": "2х местный домик",
    "4": "4х местный номер",
    "6": "6х местный номер",
    "2+5": "2+2",
    "2+4": "2+4",
    "5+4": "2+4 (домик)",
    "2+6": "2+6",
    "5+6": "2+6 (домик)",
    "4+6": "4+6",
    "2+5+4": "2+2+4",
    "2+5+6": "2+2+6",
    "2+4+6": "2+4+6",
    "5+4+6": "2+4+6 (домик)",
    "all": "Все 14 мест",
}
