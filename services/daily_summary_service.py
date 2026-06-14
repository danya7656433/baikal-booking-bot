import asyncio
from datetime import date, datetime, time
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from config import ADMIN_CHAT_ID, BOOKING_STATUS_LABELS, CANCELLED_BOOKING_STATUSES, room_type_names
from db import Booking, NotificationLog, get_session
from services.payment_service import get_payment_balance

IRKUTSK = ZoneInfo("Asia/Irkutsk")


def _is_suspicious_name(name: str | None) -> bool:
    cleaned = (name or "").strip()
    return len(cleaned) < 2 or not any(char.isalpha() for char in cleaned)


def _format_booking(booking, balance: dict) -> str:
    nights = max((booking.date_to - booking.date_from).days, 0)
    username = f" (@{booking.username})" if booking.username else ""
    warnings = []
    if _is_suspicious_name(booking.full_name):
        warnings.append("⚠️ некорректное имя")
    if not (booking.phone or "").strip() or len("".join(filter(str.isdigit, booking.phone or ""))) < 6:
        warnings.append("⚠️ некорректный телефон")
    balance_text = f"Осталось: {balance['remaining']}₽" if balance["remaining"] else "Оплачено полностью"
    if balance["overpaid"]:
        balance_text = f"Переплата: {balance['overpaid']}₽"
    lines = [
        f"Заявка #{booking.id}",
        f"👤 Гость: {(booking.full_name or 'не указано').strip()}{username}",
        f"📞 Телефон: {booking.phone or 'не указан'}",
        f"🏠 Размещение: {room_type_names.get(booking.room_type, booking.room_type)}",
        f"📅 Проживание: {booking.date_from.strftime('%d.%m.%Y')}–{booking.date_to.strftime('%d.%m.%Y')}, {nights} ноч.",
        f"👥 Гости: {booking.adults or 0} взрослых, {booking.children or 0} детей",
        f"💰 Стоимость: {balance['total']}₽",
        f"✅ Внесено: {balance['paid']}₽",
        f"↩️ Возвращено: {balance['refunds']}₽",
        f"🧾 {balance_text}",
        f"📌 Этап заявки: {BOOKING_STATUS_LABELS.get(booking.status, booking.status)}",
        f"🏡 Проживание: {booking.stay_status or 'awaiting_checkin'}",
    ]
    lines.extend(warnings)
    return "\n".join(lines)


def build_daily_summary(target_date: date) -> str:
    with get_session() as db_session:
        booking_rows = db_session.query(Booking).filter(
            Booking.deleted_at.is_(None),
            ~Booking.status.in_((*CANCELLED_BOOKING_STATUSES, "rejected")),
        ).all()
        bookings = [
            SimpleNamespace(**{
                field: getattr(booking, field)
                for field in (
                    "id", "username", "full_name", "phone", "room_type", "date_from",
                    "date_to", "adults", "children", "status", "stay_status",
                )
            })
            for booking in booking_rows
        ]
        arrivals = [b for b in bookings if b.date_from == target_date]
        departures = [b for b in bookings if b.date_to == target_date]
        staying = [b for b in bookings if b.stay_status == "checked_in"]
        debtors = []
        balances = {}
        attention = []
        for booking in bookings:
            balance = get_payment_balance(db_session, booking.id)
            balances[booking.id] = balance
            if balance["remaining"] > 0 and booking.status == "paid":
                debtors.append(booking)
            problems = []
            if _is_suspicious_name(booking.full_name):
                problems.append("некорректное имя")
            if not (booking.phone or "").strip() or len("".join(filter(str.isdigit, booking.phone or ""))) < 6:
                problems.append("некорректный телефон")
            if problems:
                attention.append(f"• #{booking.id}: {', '.join(problems)}")
    total_debt = sum(balances[b.id]["remaining"] for b in debtors)
    lines = [
        f"📊 Сводка на {target_date.strftime('%d.%m.%Y')}",
        f"\nИтого: заездов {len(arrivals)}, выездов {len(departures)}, проживают {len(staying)}, долгов {len(debtors)} на {total_debt}₽",
    ]
    if arrivals:
        lines.append("\n\n📥 Заезды сегодня\n\n" + "\n\n".join(_format_booking(b, balances[b.id]) for b in arrivals))
    if departures:
        lines.append("\n\n📤 Выезды сегодня\n\n" + "\n\n".join(_format_booking(b, balances[b.id]) for b in departures))
    if staying:
        lines.append("\n\n🏡 Сейчас проживают\n\n" + "\n\n".join(_format_booking(b, balances[b.id]) for b in staying))
    if debtors:
        lines.append("\n\n💰 Задолженности\n\n" + "\n\n".join(_format_booking(b, balances[b.id]) for b in debtors))
    if attention:
        lines.append("\n\n⚠️ Требуют внимания\n" + "\n".join(attention))
    if len(lines) == 2:
        lines.append("\nСегодня нет заездов, выездов и активных остатков.")
    return "".join(lines)


async def schedule_daily_summary(bot) -> None:
    while True:
        now = datetime.now(IRKUTSK)
        key = f"daily-summary:{now.date().isoformat()}"
        if now.time() >= time(9, 0):
            with get_session() as db_session:
                exists = db_session.query(NotificationLog).filter_by(dedupe_key=key).first()
            if not exists:
                await bot.send_message(ADMIN_CHAT_ID, build_daily_summary(now.date()))
                with get_session() as db_session:
                    db_session.add(NotificationLog(booking_id=0, kind="daily_summary", recipient="admin", dedupe_key=key))
        await asyncio.sleep(60)
