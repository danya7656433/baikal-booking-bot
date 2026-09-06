import json
import logging
import re
from typing import Any

from config import (
    ALL_ROOMS_PRICE,
    BOOKING_STATUS_LABELS,
    room_type_names,
)
from .pricing_service import calculate_revenue


def escape_markdown(text: Any) -> str:
    if text is None:
        return ""
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", str(text))


def get_duration_text(duration: int) -> str:
    if duration % 10 == 1 and duration % 100 != 11:
        return f"{duration} сутки"
    if 2 <= duration % 10 <= 4 and (duration % 100 < 10 or duration % 100 >= 20):
        return f"{duration} суток"
    return f"{duration} суток"


def get_children_beds(booking) -> list[int]:
    raw_children_beds = getattr(booking, "children_beds", None)
    if not raw_children_beds:
        return []
    try:
        children_beds = json.loads(raw_children_beds)
    except (TypeError, json.JSONDecodeError):
        logging.warning(
            "Invalid children_beds for booking #%s", getattr(booking, "id", "?")
        )
        return []
    if not isinstance(children_beds, list):
        return []
    return [1 if item else 0 for item in children_beds]


def get_booking_people_count(booking) -> int:
    return (getattr(booking, "adults", 0) or 0) + sum(get_children_beds(booking))


async def format_booking_info(booking, total: int | None = None) -> str:
    nights = (booking.date_to - booking.date_from).days
    children_beds = get_children_beds(booking)
    children_needing_beds = sum(children_beds)
    total_people = get_booking_people_count(booking)
    total_price = (
        total
        if total is not None
        else await calculate_revenue(
            booking.room_type, total_people, booking.date_from, booking.date_to
        )
    )
    price_text = (
        "Цена: рассчитана по датам и количеству платных мест.\n"
        if booking.room_type != "all"
        else f"Цена за сутки: {ALL_ROOMS_PRICE} руб.\n"
    )
    room_name = room_type_names.get(booking.room_type, booking.room_type)
    return (
        f"Гостей: {total_people} "
        f"(взрослых: {booking.adults}, детей: {booking.children}, "
        f"с местами: {children_needing_beds})\n"
        f"Номер: {room_name}\n"
        f"Заезд: {booking.date_from.strftime('%d.%m.%Y')} после 14:00\n"
        f"Выезд: {booking.date_to.strftime('%d.%m.%Y')} до 12:00 "
        f"({get_duration_text(nights)})\n"
        f"{price_text}"
        f"Сумма: {total_price} руб."
    )


async def build_admin_booking_card(booking) -> str:
    status = BOOKING_STATUS_LABELS.get(booking.status, booking.status)
    comment = booking.comment or "нет"
    admin_comment = booking.admin_comment or "нет"
    return (
        f"Заявка #{booking.id}\n"
        f"Клиент: {booking.full_name or 'не указан'}"
        f" (@{booking.username or 'без ника'})\n"
        f"Телефон: {booking.phone or 'не указан'}\n"
        f"{await format_booking_info(booking)}\n"
        f"Статус: {status}\n"
        f"Комментарий клиента: {comment}\n"
        f"Комментарий админа: {admin_comment}"
    )
