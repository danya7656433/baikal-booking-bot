from config import BOOKING_STATUS_LABELS, room_type_names
from db import Booking, get_session
from services.booking_service import get_booking_people_count, get_children_beds
from services.financial_service import calculate_required_prepayment
from services.payment_service import get_payment_balance
from services.pricing_service import calculate_revenue


PAYMENT_STATUS_LABELS = {
    "awaiting_payment": "💰 Ожидает оплаты",
    "partially_paid": "🟠 Оплачено частично",
    "paid": "🟢 Оплачено полностью",
    "overpaid": "🔵 Переплата",
    "refunded": "↩️ Оплата возвращена",
}


async def build_booking_card_data(booking_id: int) -> dict:
    with get_session() as db_session:
        booking = db_session.get(Booking, booking_id)
        if booking is None:
            raise LookupError(f"Booking #{booking_id} not found")
        if booking.calculated_total is None:
            booking.calculated_total = await calculate_revenue(
                booking.room_type,
                get_booking_people_count(booking),
                booking.date_from,
                booking.date_to,
            )
        balance = get_payment_balance(db_session, booking.id)
        required_prepayment = calculate_required_prepayment(booking, balance["total"])
        children_beds = sum(get_children_beds(booking))
        return {
            "id": booking.id,
            "user_id": booking.user_id,
            "username": booking.username,
            "full_name": booking.full_name,
            "phone": booking.phone,
            "room": room_type_names.get(booking.room_type, booking.room_type),
            "date_from": booking.date_from.strftime("%d.%m.%Y"),
            "date_to": booking.date_to.strftime("%d.%m.%Y"),
            "adults": booking.adults or 0,
            "children": booking.children or 0,
            "children_beds": children_beds,
            "people": get_booking_people_count(booking),
            "comment": booking.comment or "Без комментария",
            "admin_comment": booking.admin_comment or "нет",
            "booking_status": booking.status,
            "stay_status": booking.stay_status or "awaiting_checkin",
            "payment_status": balance["payment_status"],
            "required_prepayment": required_prepayment,
            **balance,
        }


def _status_text(data: dict) -> str:
    return PAYMENT_STATUS_LABELS.get(
        data["payment_status"],
        BOOKING_STATUS_LABELS.get(data["booking_status"], data["booking_status"]),
    )


def format_guest_booking_card(data: dict) -> str:
    return (
        f"📌 Заявка #{data['id']}\n"
        f"🏠 Номер: {data['room']}\n"
        f"📅 Заезд: {data['date_from']} после 14:00\n"
        f"📅 Выезд: {data['date_to']} до 12:00\n"
        f"👨‍👩‍👧‍👦 Всего человек: {data['people']} "
        f"(взрослых: {data['adults']}, детей: {data['children']}, "
        f"из них {data['children_beds']} с местами)\n"
        f"💰 Сумма: {data['total']}₽\n"
        f"✅ Внесено: {data['paid']}₽\n"
        f"↩️ Возвращено: {data['refunds']}₽\n"
        f"🧾 Осталось: {data['remaining']}₽\n"
        f"💬 Комментарий: {data['comment']}\n"
        f"📍 Статус: {_status_text(data)}"
    )


def format_admin_booking_card(data: dict) -> str:
    username = f"@{data['username']}" if data["username"] else "без ника"
    return (
        f"📌 Заявка #{data['id']}\n"
        f"👤 Клиент: {data['full_name'] or 'не указан'} ({username})\n"
        f"📞 Телефон: {data['phone'] or 'не указан'}\n"
        f"🏠 Номер: {data['room']}\n"
        f"📅 Даты: {data['date_from']} - {data['date_to']}\n"
        f"👥 Гости: {data['people']} (взрослые: {data['adults']}, дети: {data['children']})\n"
        f"💰 Сумма: {data['total']}₽\n"
        f"✅ Внесено: {data['paid']}₽\n"
        f"🔐 Требуемая предоплата: {data['required_prepayment']}₽\n"
        f"↩️ Возвращено: {data['refunds']}₽\n"
        f"🧾 Осталось: {data['remaining']}₽\n"
        f"📍 Статус: {_status_text(data)}\n"
        f"🏡 Проживание: {data['stay_status']}\n"
        f"💬 Комментарий: {data['comment']}\n"
        f"📝 Админ-комментарий: {data['admin_comment']}"
    )
