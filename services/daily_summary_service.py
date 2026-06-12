import asyncio
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from config import ADMIN_CHAT_ID
from db import Booking, NotificationLog, get_session
from services.payment_service import get_payment_balance

IRKUTSK = ZoneInfo("Asia/Irkutsk")


def build_daily_summary(target_date: date) -> str:
    with get_session() as db_session:
        bookings = db_session.query(Booking).all()
        arrivals = [(b.id, b.full_name or "Гость") for b in bookings if b.date_from == target_date]
        departures = [(b.id, b.full_name or "Гость") for b in bookings if b.date_to == target_date]
        staying = [(b.id, b.full_name or "Гость") for b in bookings if b.stay_status == "checked_in"]
        debtors = []
        for booking in bookings:
            balance = get_payment_balance(db_session, booking.id)
            if balance["remaining"] > 0 and booking.status == "paid":
                debtors.append((booking.id, balance["remaining"]))
    lines = [f"📊 Сводка на {target_date.strftime('%d.%m.%Y')}"]
    if arrivals:
        lines.append("\n📥 Заезды:\n" + "\n".join(f"• #{booking_id} {name}" for booking_id, name in arrivals))
    if departures:
        lines.append("\n📤 Выезды:\n" + "\n".join(f"• #{booking_id} {name}" for booking_id, name in departures))
    if staying:
        lines.append("\n🏡 Сейчас проживают:\n" + "\n".join(f"• #{booking_id} {name}" for booking_id, name in staying))
    if debtors:
        lines.append("\n💰 Остатки к оплате:\n" + "\n".join(f"• #{booking_id}: {amount}₽" for booking_id, amount in debtors))
    if len(lines) == 1:
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
