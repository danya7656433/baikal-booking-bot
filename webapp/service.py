import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import text

from config import ADMIN_CHAT_ID, ACTIVE_BOOKING_STATUSES, BOOKING_STATUS_LABELS, room_type_names
from db import Booking, BookingChange, BookingSeason, BookingYear, Room, get_session
from services.financial_service import calculate_required_prepayment
from services.inventory_service import ROOM_NAMES, is_available, room_capacity, room_parts
from services.payment_service import get_payment_balance
from services.pricing_service import calculate_revenue_in_session
from webapp.models import BookingRequest, Outbox, Receipt

IRKUTSK = ZoneInfo("Asia/Irkutsk")
PHOTO_FOLDERS = {"2": "2_room", "5": "2_urta", "4": "4_room", "6": "6_room"}
ROOT = Path(__file__).resolve().parents[1]


def today():
    return datetime.now(IRKUTSK).date()


def catalog(session):
    rooms = {room.name: room for room in session.query(Room)}
    result = []
    for code, name in ROOM_NAMES.items():
        room = rooms.get(name)
        photos = sorted((ROOT / "photos" / PHOTO_FOLDERS[code]).glob("*.jpg"))
        result.append({"code": code, "name": room_type_names[code], "capacity": room_capacity(code),
            "description": room.description if room else "", "is_available": room.is_available if room else True,
            "photos": ["/photos/" + str(photo.relative_to(ROOT / "photos")).replace("\\", "/") for photo in photos]})
    return result


def validate_search(session, data):
    if data.date_from <= today():
        raise HTTPException(422, "Бронирование доступно минимум за день до заезда")
    season = session.query(BookingSeason).first()
    year = session.query(BookingYear).first()
    if season and not season.is_active:
        raise HTTPException(409, "Приём бронирований временно закрыт")
    if year and (data.date_from.year != year.year or data.date_to > data.date_to.replace(year=year.year + 1, month=1, day=1)):
        raise HTTPException(422, f"Открыто бронирование на {year.year} год")


def options(session, data):
    validate_search(session, data)
    people = data.adults + data.children_beds
    result = []
    for code, name in room_type_names.items():
        capacity = room_capacity(code)
        if people > capacity:
            continue
        price = calculate_revenue_in_session(session, code, people, data.date_from, data.date_to)
        result.append({"code": code, "name": name, "capacity": capacity, "total": price,
            "available": is_available(session, code, data.date_from, data.date_to),
            "parts": sorted(room_parts(code)), "nights": (data.date_to - data.date_from).days})
    return sorted(result, key=lambda item: (not item["available"], item["capacity"], item["total"]))


def booking_for(session, booking_id, user):
    booking = session.get(Booking, booking_id)
    if not booking or booking.deleted_at or (not user["is_admin"] and booking.user_id != user["id"]):
        raise HTTPException(404, "Заявка не найдена")
    return booking


def snapshot(session, booking, admin=False):
    if booking.calculated_total is None:
        from services.booking_service import get_booking_people_count
        booking.calculated_total = booking.manual_total if booking.manual_total is not None else calculate_revenue_in_session(
            session, booking.room_type, get_booking_people_count(booking), booking.date_from, booking.date_to)
    balance = get_payment_balance(session, booking.id)
    receipts = session.query(Receipt).filter_by(booking_id=booking.id).order_by(Receipt.id.desc()).all()
    result = {"id": booking.id, "room_type": booking.room_type, "room_name": room_type_names.get(booking.room_type, booking.room_type),
        "date_from": booking.date_from.isoformat(), "date_to": booking.date_to.isoformat(),
        "full_name": booking.full_name, "phone": booking.phone, "comment": booking.comment,
        "adults": booking.adults, "children": booking.children, "status": booking.status,
        "status_label": BOOKING_STATUS_LABELS.get(booking.status, booking.status),
        "stay_status": booking.stay_status, "payment_deadline": booking.payment_deadline.isoformat() if booking.payment_deadline else None,
        "required_prepayment": calculate_required_prepayment(booking, balance["total"]), **balance,
        "receipts": [{"id": row.id, "status": row.status, "note": row.note, "amount": row.amount} for row in receipts]}
    if admin:
        result["admin_comment"] = booking.admin_comment
        changes = session.query(BookingChange).filter_by(booking_id=booking.id).order_by(BookingChange.id.desc()).limit(30).all()
        result["history"] = [{"kind": row.kind, "created_at": row.created_at.isoformat()} for row in changes]
    return result


def notify(session, chat_id, message):
    if chat_id:
        session.add(Outbox(chat_id=chat_id, text=message))


def record_change(session, booking, actor, kind, before=None, after=None):
    session.add(BookingChange(booking_id=booking.id, actor_id=actor, kind=kind,
        before_json=json.dumps(before, ensure_ascii=False), after_json=json.dumps(after, ensure_ascii=False)))


def create_booking(data, user):
    payload_hash = hashlib.sha256(data.model_dump_json().encode()).hexdigest()
    with get_session() as session:
        session.execute(text("BEGIN IMMEDIATE"))
        previous = session.query(BookingRequest).filter_by(user_id=user["id"], request_key=data.request_key).first()
        if previous:
            if previous.payload_hash != payload_hash:
                raise HTTPException(409, "Ключ запроса уже использован для другой заявки")
            return snapshot(session, booking_for(session, previous.booking_id, user), user["is_admin"])
        validate_search(session, data)
        if data.adults + data.children_beds > room_capacity(data.room_type):
            raise HTTPException(422, "Количество гостей превышает вместимость")
        if not is_available(session, data.room_type, data.date_from, data.date_to):
            raise HTTPException(409, "Эти даты уже заняты. Выберите другой вариант")
        total = calculate_revenue_in_session(session, data.room_type, data.adults + data.children_beds, data.date_from, data.date_to)
        if total != data.quoted_total:
            raise HTTPException(409, "Стоимость изменилась. Повторите поиск")
        count = session.query(Booking).filter(Booking.user_id == user["id"], Booking.status.in_(ACTIVE_BOOKING_STATUSES), Booking.deleted_at.is_(None)).count()
        if count >= 5 and not user["is_admin"]:
            raise HTTPException(409, "Достигнут лимит активных заявок. Свяжитесь с администратором")
        booking = Booking(user_id=user["id"], username=user.get("username"), full_name=data.full_name,
            phone=data.phone, comment=data.comment, date_from=data.date_from, date_to=data.date_to,
            room_type=data.room_type, adults=data.adults, children=data.children,
            children_beds=json.dumps([1] * data.children_beds + [0] * (data.children - data.children_beds)),
            calculated_total=total, status="new")
        session.add(booking)
        session.flush()
        session.add(BookingRequest(user_id=user["id"], request_key=data.request_key, payload_hash=payload_hash, booking_id=booking.id))
        record_change(session, booking, user["id"], "web_booking_created", after={"total": total, "rules_accepted": True})
        notify(session, ADMIN_CHAT_ID, f"Новая заявка #{booking.id}: {data.full_name}, {data.phone}. {data.date_from} - {data.date_to}. {total} руб.")
        return snapshot(session, booking)
