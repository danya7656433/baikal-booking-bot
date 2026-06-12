import json
from datetime import date

from config import room_type_names
from db import Booking, BookingChange, get_session
from services.booking_service import get_booking_people_count
from services.payment_service import get_payment_balance
from services.pricing_service import calculate_revenue


async def update_booking_accommodation(
    booking_id: int,
    actor_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    room_type: str | None = None,
    comment: str = "",
) -> dict:
    with get_session() as db_session:
        booking = db_session.get(Booking, booking_id)
        if booking is None:
            raise LookupError(f"Booking #{booking_id} not found")
        new_start = date_from or booking.date_from
        new_end = date_to or booking.date_to
        new_room = room_type or booking.room_type
        if new_end <= new_start:
            raise ValueError("Дата выезда должна быть позже даты заезда")
        if new_room not in room_type_names:
            raise ValueError("Неизвестный тип проживания")
        before = {
            "date_from": booking.date_from.isoformat(),
            "date_to": booking.date_to.isoformat(),
            "room_type": booking.room_type,
            "calculated_total": booking.calculated_total,
        }
        booking.date_from = new_start
        booking.date_to = new_end
        booking.room_type = new_room
        booking.calculated_total = await calculate_revenue(
            new_room,
            get_booking_people_count(booking),
            new_start,
            new_end,
        )
        after = {
            "date_from": new_start.isoformat(),
            "date_to": new_end.isoformat(),
            "room_type": new_room,
            "calculated_total": booking.calculated_total,
        }
        db_session.add(
            BookingChange(
                booking_id=booking_id,
                actor_id=actor_id,
                kind="accommodation_changed",
                before_json=json.dumps(before, ensure_ascii=False),
                after_json=json.dumps(after, ensure_ascii=False),
            )
        )
    with get_session() as db_session:
        balance = get_payment_balance(db_session, booking_id)
    return {**after, **balance, "comment": comment}
