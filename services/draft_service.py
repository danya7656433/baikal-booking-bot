import json
from datetime import datetime

from database import BookingDraft, session


def save_booking_draft(user_id: int, data: dict) -> None:
    draft_data = {key: value for key, value in data.items() if key != "availability_message_id"}
    payload = json.dumps(draft_data, ensure_ascii=False, default=str)
    draft = session.query(BookingDraft).filter_by(user_id=user_id).first()
    if draft:
        draft.data = payload
        draft.updated_at = datetime.now()
    else:
        draft = BookingDraft(user_id=user_id, data=payload, updated_at=datetime.now())
        session.add(draft)
    session.commit()


def get_booking_draft(user_id: int) -> dict | None:
    draft = session.query(BookingDraft).filter_by(user_id=user_id).first()
    if not draft:
        return None
    try:
        data = json.loads(draft.data or "{}")
    except json.JSONDecodeError:
        return None
    return data or None


def clear_booking_draft(user_id: int) -> None:
    draft = session.query(BookingDraft).filter_by(user_id=user_id).first()
    if draft:
        session.delete(draft)
        session.commit()
