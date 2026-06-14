import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.exc import IntegrityError

from config import ADMIN_CHAT_ID
from db import Booking, NotificationLog, get_session
from services.booking_service import get_booking_people_count
from services.payment_service import get_payment_balance
from services.pricing_service import calculate_revenue
from services.stay_service import set_stay_status

IRKUTSK = ZoneInfo("Asia/Irkutsk")


def _claim(key: str, booking_id: int, kind: str, recipient: str, days_before: int | None = None) -> bool:
    try:
        with get_session() as db_session:
            if db_session.query(NotificationLog).filter_by(dedupe_key=key).first():
                return False
            db_session.add(NotificationLog(booking_id=booking_id, kind=kind, recipient=recipient, days_before=days_before, dedupe_key=key))
        return True
    except IntegrityError:
        return False


def _balance_text(db_session, booking_id: int) -> str:
    balance = get_payment_balance(db_session, booking_id)
    return f"🧾 Осталось оплатить: {balance['remaining']}₽" if balance["remaining"] else "✅ Оплачено полностью"


def _release(key: str) -> None:
    with get_session() as db_session:
        log = db_session.query(NotificationLog).filter_by(dedupe_key=key).first()
        if log:
            db_session.delete(log)


async def _send_once(bot, chat_id: int, text: str, *, key: str, booking_id: int, kind: str, recipient: str, days_before=None, reply_markup=None) -> bool:
    if not _claim(key, booking_id, kind, recipient, days_before):
        return False
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
        return True
    except Exception:
        _release(key)
        raise


async def process_booking_notifications(bot, today=None) -> int:
    today = today or datetime.now(IRKUTSK).date()
    with get_session() as db_session:
        bookings = db_session.query(Booking).filter(Booking.status == "paid", Booking.deleted_at.is_(None)).all()
        for booking in bookings:
            if booking.calculated_total is None:
                booking.calculated_total = await calculate_revenue(
                    booking.room_type,
                    get_booking_people_count(booking),
                    booking.date_from,
                    booking.date_to,
                )
        snapshots = [(b.id, b.user_id, b.full_name, b.date_from, b.date_to, _balance_text(db_session, b.id)) for b in bookings]
    sent = 0
    for booking_id, user_id, full_name, date_from, date_to, balance_text in snapshots:
        days = (date_from - today).days
        if days in {7, 3, 1, 0}:
            recipients = {}
            if user_id:
                recipients[user_id] = "guest"
            if ADMIN_CHAT_ID:
                recipients[ADMIN_CHAT_ID] = "admin"
            for chat_id, recipient in recipients.items():
                if not chat_id:
                    continue
                key = f"checkin:{booking_id}:{days}:{recipient}"
                if await _send_once(bot, chat_id, f"📅 Заезд по заявке #{booking_id}: {date_from.strftime('%d.%m.%Y')} после 14:00.\n{balance_text}", key=key, booking_id=booking_id, kind="checkin_reminder", recipient=recipient, days_before=days):
                    sent += 1
        if date_to == today:
            recipients = {}
            if user_id:
                recipients[user_id] = "guest"
            if ADMIN_CHAT_ID:
                recipients[ADMIN_CHAT_ID] = "admin"
            for chat_id, recipient in recipients.items():
                key = f"checkout:{booking_id}:{recipient}"
                if chat_id and await _send_once(bot, chat_id, f"📤 Сегодня выезд по заявке #{booking_id} до 12:00.\n{balance_text}", key=key, booking_id=booking_id, kind="checkout", recipient=recipient, days_before=0):
                    sent += 1
        review_key = f"review:{booking_id}:guest"
        review_markup = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⭐ Оставить отзыв", callback_data=f"leave_review_{booking_id}")]])
        if date_to < today and user_id and await _send_once(bot, user_id, f"Спасибо за проживание по заявке #{booking_id}! Оставьте, пожалуйста, отзыв.", key=review_key, booking_id=booking_id, kind="review_request", recipient="guest", reply_markup=review_markup):
            set_stay_status(booking_id, "completed", None, "Запрос отзыва отправлен")
            sent += 1
    return sent


async def schedule_booking_notifications(bot) -> None:
    while True:
        await process_booking_notifications(bot)
        await asyncio.sleep(60)
