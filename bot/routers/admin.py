import logging
import os
from datetime import date
from pathlib import Path

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import ACTIVE_BOOKING_STATUSES, ADMIN_CHAT_ID, PRICE_PER_ADULT, BookingStatus
from database import (
    Booking,
    BookingSeason,
    BookingYear,
    NotificationLog,
    RoomPriceOverride,
    SupportTicket,
    session,
)
from services.backup_service import create_database_backup
from services.paths import BACKUP_DIR, LOG_PATH

router = Router()


@router.message(Command("health"))
async def admin_health(message: Message):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        await message.answer("У вас нет доступа к этой команде.")
        return

    try:
        db_ok = "ok"
        active_count = (
            session.query(Booking)
            .filter(Booking.status.in_(ACTIVE_BOOKING_STATUSES))
            .count()
        )
        awaiting_payment_count = (
            session.query(Booking)
            .filter(Booking.status == BookingStatus.AWAITING_PAYMENT.value)
            .count()
        )
        paid_count = (
            session.query(Booking)
            .filter(Booking.status == BookingStatus.PAID.value)
            .count()
        )
        open_support_count = (
            session.query(SupportTicket).filter(SupportTicket.status == "open").count()
        )
        reminder_count = (
            session.query(NotificationLog)
            .filter(NotificationLog.kind == "checkin_reminder")
            .count()
        )
        review_request_count = (
            session.query(NotificationLog)
            .filter(NotificationLog.kind == "review_request")
            .count()
        )
        occupied_dates = set()
        for booking in (
            session.query(Booking)
            .filter(Booking.status.in_(ACTIVE_BOOKING_STATUSES))
            .all()
        ):
            current = booking.date_from
            while current < booking.date_to:
                occupied_dates.add(current)
                current = current.fromordinal(current.toordinal() + 1)
        booking_year = session.query(BookingYear).first()
        current_year = booking_year.year if booking_year else "not set"
        season = session.query(BookingSeason).first()
        season_status = "active" if season and season.is_active else "inactive"
        today_override = session.query(RoomPriceOverride).filter_by(date=date.today()).first()
        today_price = today_override.price if today_override else PRICE_PER_ADULT
        error_count = 0
        last_error = "none"
        if LOG_PATH.exists():
            with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as log_file:
                for line in log_file:
                    if "ERROR" in line or "CRITICAL" in line:
                        error_count += 1
                        last_error = line.strip()[-900:]
        backup_files = list(BACKUP_DIR.glob("*.db")) + list((BACKUP_DIR / "project_full").glob("*.zip"))
        last_backup = "none"
        if backup_files:
            newest_backup = max(backup_files, key=lambda path: path.stat().st_mtime)
            last_backup = str(newest_backup)
    except Exception as error:
        db_ok = f"error: {error}"
        active_count = "unknown"
        awaiting_payment_count = "unknown"
        paid_count = "unknown"
        open_support_count = "unknown"
        reminder_count = "unknown"
        review_request_count = "unknown"
        occupied_dates = []
        current_year = "unknown"
        season_status = "unknown"
        today_price = "unknown"
        error_count = "unknown"
        last_error = "unknown"
        last_backup = "unknown"

    await message.answer(
        "🩺 Состояние бота\n\n"
        f"Бот: ok\n"
        f"База: {db_ok}\n"
        f"Год сезона: {current_year}\n"
        f"Сезон: {season_status}\n"
        f"Цена сегодня: {today_price}₽\n\n"
        f"Активные заявки: {active_count}\n"
        f"Оплаченные заявки: {paid_count}\n"
        f"Ожидают оплату: {awaiting_payment_count}\n"
        f"Занятых дат: {len(occupied_dates)}\n\n"
        f"Открытые обращения: {open_support_count}\n"
        f"Напоминания о заезде: {reminder_count}\n"
        f"Запросы отзывов: {review_request_count}\n\n"
        f"Последний бэкап: {last_backup}\n"
        f"Ошибок в логе: {error_count}\n"
        f"Последняя ошибка: {last_error}"
    )


@router.message(Command("backup_now"))
async def admin_backup_now(message: Message):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        await message.answer("У вас нет доступа к этой команде.")
        return

    try:
        backup_path = create_database_backup(timestamped=True)
        await message.answer(f"Резервная копия создана:\n{backup_path}")
    except Exception as error:
        logging.exception("Manual backup failed")
        await message.answer(f"Не удалось создать резервную копию: {error}")
