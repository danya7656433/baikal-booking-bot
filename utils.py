import asyncio
from datetime import date, datetime, timedelta
from typing import Tuple, List
import logging
import traceback
import time
from aiogram import Bot  # Явный импорт Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from database import (
    session,
    Booking,
    RoomPriceOverride,
    Review,
    AdminLog,
    BookingYear,
    BookingSeason,
    NotificationLog,
    SupportTicket,
    BlockedDate,
)
from config import (
    room_type_names,
    PRICE_PER_ADULT,
    ALL_ROOMS_PRICE,
    HOTEL_ADDRESS,
    ADMIN_CONTACT,
    LOCATION_LINK,
    ADMIN_CHAT_ID,
)
from shutil import copy
import os
import json
from dotenv import load_dotenv
from services.backup_service import create_database_backup
from services.financial_service import calculate_booking_balance
from pytz import timezone

try:
    from cachetools import TTLCache
except ImportError:
    logging.error(
        "Модуль cachetools не установлен. Установите его с помощью: pip install cachetools"
    )
    TTLCache = None

# Глобальный кэш для занятых дат
booked_dates_cache = TTLCache(maxsize=100, ttl=3600) if TTLCache else {}

ROOM_DEPENDENCIES = {
    "2": ["2", "2+5", "2+4", "2+6", "2+5+4", "2+5+6", "2+4+6", "all"],
    "5": ["5", "2+5", "5+4", "5+6", "2+5+4", "2+5+6", "5+4+6", "all"],
    "4": ["4", "2+4", "5+4", "4+6", "2+5+4", "2+4+6", "5+4+6", "all"],
    "6": ["6", "2+6", "5+6", "4+6", "2+5+6", "2+4+6", "5+4+6", "all"],
    "2+5": ["2", "5", "2+5", "2+4", "5+4", "2+6", "5+6", "2+5+4", "2+5+6", "2+4+6", "5+4+6", "all"],
    "2+4": ["2", "4", "2+5", "2+4", "5+4", "4+6", "2+5+4", "2+4+6", "5+4+6", "all"],
    "5+4": ["5", "4", "2+5", "2+4", "5+4", "4+6", "2+5+4", "2+4+6", "5+4+6", "all"],
    "2+6": ["2", "6", "2+5", "2+6", "5+6", "4+6", "2+5+6", "2+4+6", "5+4+6", "all"],
    "5+6": ["5", "6", "2+5", "2+6", "5+6", "4+6", "2+5+6", "2+4+6", "5+4+6", "all"],
    "4+6": ["4", "6", "2+4", "5+4", "2+6", "5+6", "4+6", "2+5+4", "2+5+6", "2+4+6", "5+4+6", "all"],
    "2+5+4": ["2", "5", "4", "2+5", "2+4", "5+4", "4+6", "2+5+4", "2+4+6", "5+4+6", "all"],
    "2+5+6": ["2", "5", "6", "2+5", "2+6", "5+6", "4+6", "2+5+6", "2+4+6", "5+4+6", "all"],
    "2+4+6": ["2", "4", "6", "2+4", "2+6", "4+6", "2+5+4", "2+5+6", "2+4+6", "5+4+6", "all"],
    "5+4+6": ["5", "4", "6", "5+4", "5+6", "4+6", "2+5+4", "2+5+6", "2+4+6", "5+4+6", "all"],
    "all": ["2", "5", "4", "6", "2+5", "2+4", "5+4", "2+6", "5+6", "4+6", "2+5+4", "2+5+6", "2+4+6", "5+4+6", "all"],
}


def get_duration_text(duration: int) -> str:
    """Возвращает правильное склонение для слова 'сутки'."""
    if duration % 10 == 1 and duration % 100 != 11:
        return f"{duration} сутки"
    elif 2 <= duration % 10 <= 4 and (duration % 100 < 10 or duration % 100 >= 20):
        return f"{duration} суток"
    else:
        return f"{duration} суток"


async def format_booking_info(booking: Booking, total: int = None) -> str:
    nights = (booking.date_to - booking.date_from).days
    children_beds = get_children_beds(booking)
    children_needing_beds = sum(children_beds)
    total_people = booking.adults + children_needing_beds
    total_price = (
        total
        if total is not None
        else await calculate_revenue(
            booking.room_type, total_people, booking.date_from, booking.date_to
        )
    )
    price_per_day_text = (
        "💰 Стоимость рассчитана по выбранным датам и количеству платных мест.\n"
        if booking.room_type != "all"
        else f"💰 Цена за сутки: {ALL_ROOMS_PRICE}₽\n"
    )
    return (
        f"👨‍👩‍👧‍👦 Всего человек: {total_people} (взрослых: {booking.adults}, детей: {booking.children}, из них {children_needing_beds} с местами)\n"
        f"🏠 Номер: {room_type_names[booking.room_type]}\n"
        f"📅 Заезд: {booking.date_from.strftime('%d.%m.%Y')} после 14:00\n"
        f"📅 Выезд: {booking.date_to.strftime('%d.%m.%Y')} до 12:00 ({get_duration_text(nights)})\n"
        f"{price_per_day_text}"
        f"💰 Сумма: {total_price}₽"
    )


def get_children_beds(booking: Booking) -> list:
    """
    Безопасно извлекает children_beds из объекта Booking.
    Возвращает пустой список в случае ошибки или отсутствия данных.
    """
    children_beds = []
    if hasattr(booking, "children_beds") and booking.children_beds is not None:
        try:
            children_beds = json.loads(booking.children_beds)
            if not isinstance(children_beds, list):
                logging.error(
                    f"children_beds не является списком для заявки #{booking.id}: {children_beds}"
                )
                children_beds = []
            logging.debug(
                f"Извлечено children_beds для заявки #{booking.id}: {children_beds}"
            )
        except json.JSONDecodeError as e:
            logging.error(
                f"Ошибка парсинга children_beds для заявки #{booking.id}: {str(e)}"
            )
            children_beds = []
    else:
        logging.warning(f"children_beds отсутствует или NULL для заявки #{booking.id}")
    return children_beds


async def get_booked_dates(
    room_type: str,
) -> Tuple[List[datetime.date], List[datetime.date]]:
    start_time = datetime.now()
    cache_key = f"{room_type}_{date.today().isoformat()}"

    if cache_key in booked_dates_cache:
        del booked_dates_cache[cache_key]
        logging.info(f"Cleared cache for {room_type}")

    occupied_dates: List[datetime.date] = []
    checkin_dates: List[datetime.date] = []

    room_capacities = {
        "2": 2,
        "5": 2,
        "4": 4,
        "6": 6,
        "2+5": 4,
        "2+4": 6,
        "5+4": 6,
        "2+6": 8,
        "5+6": 8,
        "4+6": 10,
        "2+5+4": 8,
        "2+5+6": 10,
        "2+4+6": 12,
        "5+4+6": 12,
        "all": 14,
    }

    # Получаем текущий год из базы
    current_year = session.query(BookingYear).first().year

    with session as db_session:
        bookings = (
            db_session.query(Booking)
            .filter(
                Booking.status.in_(["new", "pending", "awaiting_payment", "paid"]),
                Booking.date_from >= datetime(current_year, 1, 1),
                Booking.date_from < datetime(current_year + 1, 1, 1),
            )
            .all()
        )
        logging.debug(
            f"Active bookings for {room_type} in {current_year}: {[f'ID={b.id}, room_type={b.room_type}, date_from={b.date_from}, date_to={b.date_to}, status={b.status}' for b in bookings]}"
        )

        for booking in bookings:
            logging.debug(
                f"Processing booking #{booking.id}: date_from={booking.date_from}, date_to={booking.date_to}, room_type={booking.room_type}, status={booking.status}"
            )
            current = booking.date_from
            while current <= booking.date_to - timedelta(days=1):
                logging.debug(
                    f"Checking date {current.isoformat()} for room_type {room_type}, booking room_type {booking.room_type}"
                )
                if room_type in ROOM_DEPENDENCIES[booking.room_type]:
                    if current not in occupied_dates:
                        occupied_dates.append(current)
                        logging.info(
                            f"Date {current.isoformat()} unavailable for {room_type} due to dependency on {booking.room_type} (booking #{booking.id})"
                        )
                    else:
                        logging.debug(
                            f"Date {current.isoformat()} already in occupied_dates for {room_type}"
                        )
                else:
                    logging.debug(
                        f"Room {room_type} not dependent on {booking.room_type}, skipping date {current.isoformat()}"
                    )

                if (
                    current == booking.date_from
                    and current not in checkin_dates
                    and booking.status in ["pending", "paid"]
                ):
                    if room_type in ROOM_DEPENDENCIES.get(booking.room_type, []):
                        checkin_dates.append(current)
                        logging.debug(
                            f"Added check-in date {current.isoformat()} for booking #{booking.id} (room_type={booking.room_type})"
                        )

                current += timedelta(days=1)

        blocked_dates = (
            db_session.query(BlockedDate)
            .filter(
                BlockedDate.date >= datetime(current_year, 1, 1).date(),
                BlockedDate.date < datetime(current_year + 1, 1, 1).date(),
            )
            .all()
        )
        for blocked in blocked_dates:
            if blocked.room_type in {"all_rooms", room_type} or room_type in ROOM_DEPENDENCIES.get(blocked.room_type, []):
                if blocked.date not in occupied_dates:
                    occupied_dates.append(blocked.date)

        if room_type != "all":
            date_to_capacity = {}
            for booking in bookings:
                current = booking.date_from
                while current <= booking.date_to - timedelta(days=1):
                    if current not in date_to_capacity:
                        date_to_capacity[current] = 0
                    date_to_capacity[current] += room_capacities[booking.room_type]
                    current += timedelta(days=1)

            for current_date, total_capacity in date_to_capacity.items():
                if total_capacity + room_capacities[room_type] > room_capacities["all"]:
                    if current_date not in occupied_dates:
                        occupied_dates.append(current_date)
                        logging.info(
                            f"Date {current_date.isoformat()} unavailable for {room_type}: total capacity={total_capacity}, required={room_capacities[room_type]}"
                        )

        if room_type == "all":
            date_to_capacity = {}
            for booking in bookings:
                current = booking.date_from
                while current <= booking.date_to - timedelta(days=1):
                    if current not in date_to_capacity:
                        date_to_capacity[current] = 0
                    date_to_capacity[current] += room_capacities[booking.room_type]
                    current += timedelta(days=1)

            for current_date, total_capacity in date_to_capacity.items():
                if total_capacity >= room_capacities["all"]:
                    if current_date not in occupied_dates:
                        occupied_dates.append(current_date)
                        logging.info(
                            f"Date {current_date.isoformat()} unavailable for all: full occupancy"
                        )

    occupied_dates.sort()
    checkin_dates.sort()
    logging.debug(
        f"Final occupied dates for {room_type}: {[d.isoformat() for d in occupied_dates]}"
    )
    logging.debug(
        f"Final check-in dates for {room_type}: {[d.isoformat() for d in checkin_dates]}"
    )
    booked_dates_cache[cache_key] = (occupied_dates, checkin_dates)
    logging.info(
        f"Saved dates to cache for {room_type}: {len(occupied_dates)} occupied, {len(checkin_dates)} check-in dates"
    )
    logging.info(
        f"get_booked_dates for {room_type} completed in {(datetime.now() - start_time).total_seconds()} seconds"
    )
    return booked_dates_cache[cache_key]


async def is_room_available(
    room_type: str,
    date_from: date,
    date_to: date,
    occupied_dates: List[str],
    checkin_dates: List[str],
) -> bool:
    logging.debug(
        f"Checking availability for {room_type} from {date_from.isoformat()} to {date_to.isoformat()}"
    )
    try:
        occupied = [datetime.strptime(d, "%Y-%m-%d").date() for d in occupied_dates]
        checkin = [datetime.strptime(d, "%Y-%m-%d").date() for d in checkin_dates]
        logging.debug(
            f"Occupied dates: {[d.isoformat() for d in occupied]}, Check-in dates: {[d.isoformat() for d in checkin]}"
        )

        current_date = date_from
        while current_date <= date_to:
            is_occupied = current_date in occupied
            is_checkin = False
            # Проверяем, если текущая дата — это дата заезда другого бронирования
            if current_date != date_from and current_date in checkin:
                with session as db_session:
                    bookings = (
                        db_session.query(Booking)
                        .filter(
                            Booking.date_from == current_date,
                            Booking.status.in_(["pending", "paid"]),
                        )
                        .all()
                    )
                    for booking in bookings:
                        if (
                            room_type in ROOM_DEPENDENCIES.get(booking.room_type, [])
                            or booking.room_type == room_type
                        ):
                            is_checkin = True
                            logging.debug(
                                f"Check-in blocked for {room_type} on {current_date.isoformat()} by booking #{booking.id} ({booking.room_type})"
                            )
                            break
            logging.debug(
                f"Checking date {current_date.isoformat()}: occupied={is_occupied}, checkin={is_checkin}"
            )
            if is_occupied or is_checkin:
                logging.debug(
                    f"Period unavailable: date {current_date.isoformat()} occupied (occupied={is_occupied}, checkin={is_checkin})"
                )
                return False
            current_date += timedelta(days=1)
        logging.debug(
            f"Period {date_from.isoformat()}–{date_to.isoformat()} available for room {room_type}"
        )
        return True
    except Exception as e:
        logging.error(f"Error in is_room_available: {str(e)}\n{traceback.format_exc()}")
        raise


async def calculate_revenue(
    room_type: str, total_people: int, date_from: date, date_to: date
) -> int:
    total = 0
    current_date = date_from
    with session as db_session:
        while current_date < date_to:
            price_override = (
                db_session.query(RoomPriceOverride).filter_by(date=current_date).first()
            )
            price_per_person = (
                price_override.price if price_override else PRICE_PER_ADULT
            )

            # Специальная логика для типа "all"
            if room_type == "all":
                # Для 8–10 человек: фиксированная цена 18,000₽ за сутки
                if 8 <= total_people <= 10:
                    total += ALL_ROOMS_PRICE  # 18,000₽
                    logging.debug(
                        f"Date {current_date}: Fixed price for 'all' (8–10 people) = {ALL_ROOMS_PRICE}₽"
                    )
                # Для 11–14 человек: 1,500₽ с человека или цена из календаря.
                else:
                    total += total_people * price_per_person
                    logging.debug(
                        f"Date {current_date}: Price for 'all' (11–14 people) = {total_people} * {price_per_person} = {total_people * price_per_person}₽"
                    )
            else:
                # Для других типов номеров: стандартная цена (1,500₽ с человека или по переопределению)
                total += total_people * price_per_person
                logging.debug(
                    f"Date {current_date}: Price for {room_type} = {total_people} * {price_per_person} = {total_people * price_per_person}₽"
                )

            current_date += timedelta(days=1)
    logging.info(
        f"Total revenue for {room_type}, {total_people} people, {date_from}–{date_to}: {total}₽"
    )
    return total


async def get_optimal_room_combinations(
    total_people: int,
    date_from: date = None,
    date_to: date = None,
    duration: int = None,
) -> List[str]:
    """Возвращает варианты проживания по утвержденной матрице вместимости."""
    logging.debug(
        f"get_optimal_room_combinations called with total_people={total_people}, duration={duration}, date_from={date_from}, date_to={date_to}"
    )

    if date_from and date_to:
        duration = (date_to - date_from).days
        logging.debug(f"Calculated duration from dates: {duration}")
    elif duration is None:
        duration = 1
        logging.debug(f"Default duration set: {duration}")

    short_stay = duration <= 2
    if total_people >= 13:
        result = ["all"]
    elif short_stay:
        matrix = {
            1: ["2", "5"],
            2: ["2", "5", "4"],
            3: ["4", "2+5"],
            4: ["4", "6", "2+5"],
            5: ["6", "2+4", "5+4"],
            6: ["6", "2+5+4", "2+4", "5+4"],
            7: ["2+5+4", "2+6", "5+6"],
            8: ["2+5+4", "2+6", "5+6", "all"],
            9: ["4+6", "2+5+6", "all"],
            10: ["2+4+6", "2+5+6", "4+6", "all"],
            11: ["2+4+6", "5+4+6", "all"],
            12: ["2+4+6", "5+4+6", "all"],
        }
        result = matrix.get(total_people, ["all"])
    else:
        matrix = {
            1: ["2", "5"],
            2: ["2", "5"],
            3: ["4"],
            4: ["4"],
            5: ["6", "2+4", "5+4"],
            6: ["6", "2+4", "5+4"],
            7: ["2+5+4", "2+6", "5+6"],
            8: ["2+5+4", "4+6", "all"],
            9: ["4+6", "2+5+6", "all"],
            10: ["4+6", "2+5+6", "all"],
            11: ["2+4+6", "5+4+6", "all"],
            12: ["2+4+6", "5+4+6", "all"],
        }
        result = matrix.get(total_people, ["all"])

    logging.info(
        f"Оптимальные номера для {total_people} человек на {duration} суток: {result}"
    )
    return result


async def backup_database() -> str:
    try:
        backup_file = create_database_backup()
        logging.info(f"Резервная копия создана или обновлена: {backup_file}")
        return backup_file
    except Exception as e:
        logging.error(f"Ошибка в backup_database: {str(e)}\n{traceback.format_exc()}")
        raise


async def schedule_reminders(bot: Bot):
    logging.info("Background task schedule_reminders started")
    while True:
        try:
            with session as db_session:
                bookings = db_session.query(Booking).filter_by(status="paid").all()
                today = datetime.now().date()
                for booking in bookings:
                    days_before = (booking.date_from - today).days
                    if days_before not in {7, 3, 1}:
                        continue
                    existing = (
                        db_session.query(NotificationLog)
                        .filter_by(
                            booking_id=booking.id,
                            kind="checkin_reminder",
                            days_before=days_before,
                        )
                        .first()
                    )
                    if existing:
                        continue

                    day_text = {7: "через 7 дней", 3: "через 3 дня", 1: "завтра"}[days_before]
                    total_people = booking.adults + sum(get_children_beds(booking))
                    calculated_total = await calculate_revenue(
                        booking.room_type,
                        total_people,
                        booking.date_from,
                        booking.date_to,
                    )
                    balance = calculate_booking_balance(booking, calculated_total)
                    remaining_text = (
                        f"\n🧾 Остаток к оплате: {balance['remaining']}₽"
                        if balance["remaining"] > 0
                        else "\n✅ Оплачено полностью"
                    )
                    guest_text = (
                        f"📅 Напоминаем: ваш заезд по заявке #{booking.id} {day_text}, "
                        f"{booking.date_from.strftime('%d.%m.%Y')} после 14:00."
                        f"{remaining_text}"
                    )
                    admin_text = (
                        f"📅 Заезд {day_text}: заявка #{booking.id}\n"
                        f"Гость: {booking.full_name} (@{booking.username or 'без ника'})\n"
                        f"Телефон: {booking.phone}\n"
                        f"Номер: {room_type_names.get(booking.room_type, booking.room_type)}\n"
                        f"Даты: {booking.date_from.strftime('%d.%m.%Y')} - {booking.date_to.strftime('%d.%m.%Y')}"
                        f"{remaining_text}"
                    )
                    try:
                        await bot.send_message(booking.user_id, guest_text)
                    except Exception as e:
                        logging.warning(
                            "Failed to send guest check-in reminder for booking #%s: %s",
                            booking.id,
                            e,
                        )
                    await bot.send_message(ADMIN_CHAT_ID, admin_text)
                    db_session.add(
                        NotificationLog(
                            booking_id=booking.id,
                            kind="checkin_reminder",
                            days_before=days_before,
                        )
                    )
                    db_session.commit()
                    logging.info(
                        "Check-in reminder sent for booking #%s, days_before=%s",
                        booking.id,
                        days_before,
                    )
        except Exception as e:
            logging.error(
                f"Ошибка в schedule_reminders: {str(e)}\n{traceback.format_exc()}"
            )
        await asyncio.sleep(24 * 3600)


async def schedule_backups():
    logging.info("Фоновая задача schedule_backups запущена")
    while True:
        try:
            await backup_database()
        except Exception as e:
            logging.error(
                f"Ошибка в schedule_backups: {str(e)}\n{traceback.format_exc()}"
            )
        await asyncio.sleep(24 * 3600)


async def schedule_reviews(bot: Bot):
    logging.info("Background task schedule_reviews started")
    while True:
        try:
            today = datetime.now().date()
            with session as db_session:
                bookings = (
                    db_session.query(Booking)
                    .filter(Booking.status == "paid", Booking.date_to <= today)
                    .all()
                )
                logging.info(
                    "Completed paid bookings for review check: %s %s",
                    len(bookings),
                    [booking.id for booking in bookings],
                )
                for booking in bookings:
                    existing_review = (
                        db_session.query(Review)
                        .filter_by(booking_id=booking.id)
                        .first()
                    )
                    existing_request = (
                        db_session.query(NotificationLog)
                        .filter_by(booking_id=booking.id, kind="review_request")
                        .first()
                    )
                    if existing_review or existing_request:
                        continue
                    await bot.send_message(
                        booking.user_id,
                        f"Спасибо за проживание по заявке #{booking.id}!\n"
                        f"Пожалуйста, оставьте отзыв о вашем отдыхе.\n\n"
                        f"Также будем рады отзыву на 2GIS: {LOCATION_LINK}",
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[
                                [
                                    InlineKeyboardButton(
                                        text="⭐ Оставить отзыв",
                                        callback_data=f"leave_review_{booking.id}",
                                    )
                                ]
                            ]
                        ),
                    )
                    db_session.add(
                        NotificationLog(
                            booking_id=booking.id,
                            kind="review_request",
                            days_before=None,
                        )
                    )
                    db_session.commit()
                    logging.info("Review request sent for booking #%s", booking.id)
        except Exception as e:
            logging.error(
                f"Ошибка в schedule_reviews: {str(e)}\n{traceback.format_exc()}"
            )
        await asyncio.sleep(24 * 3600)


async def check_support_timeouts(bot: Bot):
    logging.info("Background task check_support_timeouts started")
    while True:
        try:
            now = datetime.now()
            with session as db_session:
                tickets = (
                    db_session.query(SupportTicket)
                    .filter(SupportTicket.status == "open", SupportTicket.expires_at <= now)
                    .all()
                )
                for ticket in tickets:
                    if ticket.user_message_id:
                        try:
                            await bot.edit_message_reply_markup(
                                chat_id=ticket.user_id,
                                message_id=ticket.user_message_id,
                                reply_markup=None,
                            )
                        except Exception as e:
                            logging.warning("Failed to remove support buttons #%s: %s", ticket.id, e)
                    await bot.send_message(
                        ticket.user_id,
                        "✅ Вопрос автоматически закрыт, потому что 15 минут не было ответа.",
                    )
                    await bot.send_message(
                        ADMIN_CHAT_ID,
                        f"✅ Обращение пользователя {ticket.user_id} автоматически закрыто.",
                    )
                    ticket.status = "closed_auto"
                    ticket.closed_at = now
                if tickets:
                    db_session.commit()
                    logging.info("Closed expired support tickets: %s", len(tickets))
        except Exception as e:
            logging.error(
                f"Ошибка в check_support_timeouts: {str(e)}\n{traceback.format_exc()}"
            )
        await asyncio.sleep(60)


async def schedule_season_end(bot: Bot):
    logging.info("Задача schedule_season_end запущена")
    while True:
        today = datetime.now().date()
        current_year = session.query(BookingYear).first().year
        season_end_date = date(current_year, 9, 1)
        if today == season_end_date:
            season = session.query(BookingSeason).first()
            if season.is_active:
                season.is_active = False
                session.commit()
                await bot.send_message(
                    ADMIN_CHAT_ID, "🌞 Сезон бронирования автоматически завершён."
                )
                logging.info("Сезон бронирования автоматически завершён")
        await asyncio.sleep(24 * 3600)


def get_optimal_rooms(total_people: int, duration: int) -> list:
    """
    Возвращает список оптимальных типов номеров на основе количества людей и длительности бронирования.

    Args:
        total_people (int): Общее количество людей (взрослые + дети с кроватями).
        duration (int): Длительность бронирования в сутках.

    Returns:
        list: Список подходящих типов номеров.
    """
    # Простая логика на основе количества людей
    if total_people <= 2:
        return ["2", "5"]
    elif total_people <= 4:
        return ["2", "4"]
    elif total_people <= 6:
        return ["2", "4", "6"]
    elif total_people <= 8:
        return ["2+4", "2+6", "4+6"]
    else:
        return ["all"]


def clear_booked_dates_cache():
    booked_dates_cache.clear()
    logging.info("Кэш booked_dates_cache полностью очищен")


class TelegramHandler(logging.Handler):
    def __init__(self, bot: Bot, level=logging.NOTSET, rate_limit_seconds=60):
        super().__init__(level)
        self.bot = bot
        self.rate_limit_seconds = rate_limit_seconds
        self.last_sent = 0

    def emit(self, record):
        try:
            current_time = time.time()
            if current_time - self.last_sent < self.rate_limit_seconds:
                return  # Пропускаем, если прошло меньше времени, чем rate_limit_seconds

            log_message = self.format(record)
            if len(log_message) > 4096:
                log_message = log_message[:4000] + "... (сообщение обрезано)"

            # Создаём асинхронную задачу для отправки сообщения
            asyncio.create_task(
                self.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"⚠️ Лог: {log_message}",
                    parse_mode="HTML",
                )
            )
            self.last_sent = current_time
        except TelegramAPIError as e:
            logging.error(f"Failed to send log message to Telegram: {str(e)}")
        except Exception as e:
            logging.error(f"Unexpected error in TelegramHandler: {str(e)}")
