"""Shared inventory rules. Checkout dates are exclusive."""

from datetime import date

from config import ACTIVE_BOOKING_STATUSES, room_type_names
from db.models import BlockedDate, Booking, Room

ROOM_CAPACITIES = {"2": 2, "5": 2, "4": 4, "6": 6}
ROOM_NAMES = {"2": "2-местный", "5": "Домик на 2 места", "4": "4-местный", "6": "6-местный"}


def room_parts(room_type: str) -> frozenset[str]:
    if room_type not in room_type_names:
        raise ValueError("Неизвестный вариант проживания")
    return frozenset(ROOM_CAPACITIES if room_type == "all" else room_type.split("+"))


def room_capacity(room_type: str) -> int:
    return sum(ROOM_CAPACITIES[part] for part in room_parts(room_type))


def rooms_overlap(first: str, second: str) -> bool:
    return bool(room_parts(first) & room_parts(second))


def is_available(db_session, room_type: str, start: date, end: date, exclude_id=None) -> bool:
    room_parts(room_type)
    if end <= start:
        raise ValueError("Дата выезда должна быть позже даты заезда")
    disabled = db_session.query(Room).filter(
        Room.name.in_([ROOM_NAMES[part] for part in room_parts(room_type)]),
        Room.is_available.is_(False),
    ).first()
    if disabled:
        return False
    bookings = db_session.query(Booking).filter(
        Booking.status.in_(ACTIVE_BOOKING_STATUSES),
        Booking.deleted_at.is_(None),
        Booking.date_from < end,
        Booking.date_to > start,
    )
    if exclude_id is not None:
        bookings = bookings.filter(Booking.id != exclude_id)
    if any(rooms_overlap(room_type, booking.room_type) for booking in bookings):
        return False
    blocked = db_session.query(BlockedDate).filter(
        BlockedDate.date >= start, BlockedDate.date < end,
    )
    return not any(
        day.room_type == "all_rooms" or rooms_overlap(room_type, day.room_type)
        for day in blocked
    )
