import json
from datetime import datetime, time
from zoneinfo import ZoneInfo

from db import Booking, BookingChange, get_session

IRKUTSK = ZoneInfo("Asia/Irkutsk")
STAY_STATUSES = {"awaiting_checkin", "checked_in", "checked_out", "completed"}


def calculate_due_stay_status(booking, now: datetime) -> str:
    local_now = now.astimezone(IRKUTSK) if now.tzinfo else now.replace(tzinfo=IRKUTSK)
    checkout = datetime.combine(booking.date_to, time(12, 0), IRKUTSK)
    checkin = datetime.combine(booking.date_from, time(14, 0), IRKUTSK)
    current = booking.stay_status or "awaiting_checkin"
    if current == "completed":
        return current
    if local_now >= checkout:
        return "checked_out"
    if local_now >= checkin:
        return "checked_in"
    return "awaiting_checkin"


def set_stay_status(booking_id: int, status: str, actor_id: int | None, comment: str = "") -> None:
    if status not in STAY_STATUSES:
        raise ValueError("Unknown stay status")
    with get_session() as db_session:
        booking = db_session.get(Booking, booking_id)
        if booking is None:
            raise LookupError(f"Booking #{booking_id} not found")
        before = booking.stay_status or "awaiting_checkin"
        if before == status:
            return
        booking.stay_status = status
        booking.stay_status_changed_at = datetime.now()
        db_session.add(
            BookingChange(
                booking_id=booking_id,
                actor_id=actor_id,
                kind="stay_status_changed",
                before_json=json.dumps({"stay_status": before}, ensure_ascii=False),
                after_json=json.dumps({"stay_status": status, "comment": comment}, ensure_ascii=False),
            )
        )


def apply_due_stay_transitions(now: datetime | None = None) -> list[int]:
    now = now or datetime.now(IRKUTSK)
    changed = []
    with get_session() as db_session:
        bookings = db_session.query(Booking).filter(
            Booking.status.in_(("paid", "completed")),
            Booking.deleted_at.is_(None),
        ).all()
        for booking in bookings:
            due = calculate_due_stay_status(booking, now)
            current = booking.stay_status or "awaiting_checkin"
            if due == current:
                continue
            booking.stay_status = due
            booking.stay_status_changed_at = datetime.now()
            db_session.add(
                BookingChange(
                    booking_id=booking.id,
                    actor_id=None,
                    kind="stay_status_changed",
                    before_json=json.dumps({"stay_status": current}),
                    after_json=json.dumps({"stay_status": due}),
                )
            )
            changed.append(booking.id)
    return changed


async def schedule_stay_transitions() -> None:
    import asyncio
    while True:
        apply_due_stay_transitions()
        await asyncio.sleep(60)
