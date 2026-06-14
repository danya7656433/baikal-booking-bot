import json
from datetime import date, datetime

from config import room_type_names
from db import Booking, BookingChange, get_session
from services.booking_service import get_booking_people_count
from services.payment_service import get_payment_balance
from services.pricing_service import calculate_revenue

BOOKING_STAGES = {
    "new", "pending", "awaiting_payment", "awaiting_payment_confirmation",
    "paid", "awaiting_cancellation", "cancelled", "canceled", "rejected",
}
EDITABLE_FIELDS = {
    "full_name", "phone", "adults", "children", "comment", "admin_comment", "manual_total",
}


def update_booking_fields(booking_id: int, actor_id: int, **changes) -> None:
    unknown = set(changes) - EDITABLE_FIELDS
    if unknown:
        raise ValueError(f"Нельзя изменить поля: {', '.join(sorted(unknown))}")
    with get_session() as db_session:
        booking = db_session.get(Booking, booking_id)
        if booking is None:
            raise LookupError(f"Booking #{booking_id} not found")
        before = {field: getattr(booking, field) for field in changes}
        for field, value in changes.items():
            if field in {"adults", "children", "manual_total"}:
                value = int(value)
                if value < 0 or (field == "adults" and value == 0):
                    raise ValueError("Числовое значение недопустимо")
            elif isinstance(value, str):
                value = value.strip()
                if field in {"full_name", "phone"} and not value:
                    raise ValueError("Значение не может быть пустым")
            setattr(booking, field, value)
        if "manual_total" in changes:
            booking.calculated_total = int(changes["manual_total"])
        after = {field: getattr(booking, field) for field in changes}
        db_session.add(BookingChange(
            booking_id=booking_id,
            actor_id=actor_id,
            kind="booking_fields_changed",
            before_json=json.dumps(before, ensure_ascii=False),
            after_json=json.dumps(after, ensure_ascii=False),
        ))


def change_booking_stage(booking_id: int, actor_id: int, stage: str) -> None:
    if stage not in BOOKING_STAGES:
        raise ValueError("Неизвестный этап заявки")
    with get_session() as db_session:
        booking = db_session.get(Booking, booking_id)
        if booking is None:
            raise LookupError(f"Booking #{booking_id} not found")
        before = booking.status
        if before == stage:
            return
        booking.status = stage
        if stage != "awaiting_payment":
            booking.payment_deadline = None
        db_session.add(BookingChange(
            booking_id=booking_id,
            actor_id=actor_id,
            kind="booking_stage_changed",
            before_json=json.dumps({"status": before}, ensure_ascii=False),
            after_json=json.dumps({"status": stage}, ensure_ascii=False),
        ))


def soft_delete_booking(booking_id: int, actor_id: int, reason: str = "") -> None:
    with get_session() as db_session:
        booking = db_session.get(Booking, booking_id)
        if booking is None:
            raise LookupError(f"Booking #{booking_id} not found")
        if booking.deleted_at is not None:
            return
        booking.deleted_at = datetime.now()
        booking.deleted_by = actor_id
        booking.deletion_reason = reason.strip() or None
        db_session.add(BookingChange(
            booking_id=booking_id,
            actor_id=actor_id,
            kind="booking_deleted",
            before_json=json.dumps({"deleted_at": None}, ensure_ascii=False),
            after_json=json.dumps({"deleted_at": booking.deleted_at.isoformat(), "reason": booking.deletion_reason}, ensure_ascii=False),
        ))


def restore_booking(booking_id: int, actor_id: int) -> None:
    with get_session() as db_session:
        booking = db_session.get(Booking, booking_id)
        if booking is None:
            raise LookupError(f"Booking #{booking_id} not found")
        if booking.deleted_at is None:
            return
        deleted_at = booking.deleted_at.isoformat()
        booking.deleted_at = None
        booking.deleted_by = None
        booking.deletion_reason = None
        db_session.add(BookingChange(
            booking_id=booking_id,
            actor_id=actor_id,
            kind="booking_restored",
            before_json=json.dumps({"deleted_at": deleted_at}, ensure_ascii=False),
            after_json=json.dumps({"deleted_at": None}, ensure_ascii=False),
        ))


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
