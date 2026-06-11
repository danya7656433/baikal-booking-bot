import logging
import json
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.states import BookingStates
from config import ACTIVE_BOOKING_STATUSES, BookingStatus, room_type_names
from database import Booking, BookingHistory, session
from services.booking_service import get_children_beds
from services.financial_service import calculate_booking_balance, payment_status_label
from utils import ROOM_DEPENDENCIES, calculate_revenue, clear_booked_dates_cache, get_optimal_room_combinations

router = Router()


@router.callback_query(F.data == "my_bookings")
async def my_bookings(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    session.expire_all()
    bookings = (
        session.query(Booking)
        .filter(
            Booking.user_id == user_id,
            Booking.status.in_(ACTIVE_BOOKING_STATUSES),
        )
        .order_by(Booking.created_at.asc())
        .all()
    )

    await state.update_data(previous_menu="main_menu")
    if not bookings:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
            ]
        )
        try:
            await callback.message.edit_text(
                "📋 У вас нет активных заявок.", reply_markup=kb
            )
        except TelegramBadRequest as e:
            if "there is no text in the message to edit" in str(e):
                await callback.message.delete()
                await callback.message.answer(
                    "📋 У вас нет активных заявок.", reply_markup=kb
                )
            else:
                raise
        logging.info("User %s viewed own bookings: count=0", user_id)
        await callback.answer()
        return

    await callback.message.delete()
    status_labels = {
        "new": "🟡 Ожидает подтверждения администратора",
        "pending": "🟡 Ожидает подтверждения",
        "awaiting_payment": "💰 Ожидает оплаты",
        "awaiting_payment_confirmation": "📸 Ожидает подтверждения оплаты",
        "paid": "🟢 Оплачено, ждём в гости",
        "awaiting_cancellation": "⛔ Ожидает отмены",
    }

    for idx, booking in enumerate(bookings):
        children_beds = get_children_beds(booking)
        children_needing_beds = sum(children_beds)
        total_people = booking.adults + children_needing_beds
        calculated_total = await calculate_revenue(
            booking.room_type, total_people, booking.date_from, booking.date_to
        )
        balance = calculate_booking_balance(booking, calculated_total)
        status = payment_status_label(balance) or status_labels.get(booking.status, booking.status)
        text = (
            f"📌 Заявка #{booking.id}\n"
            f"🏠 Номер: {room_type_names[booking.room_type]}\n"
            f"📅 Заезд: {booking.date_from.strftime('%d.%m.%Y')} после 14:00\n"
            f"📅 Выезд: {booking.date_to.strftime('%d.%m.%Y')} до 12:00\n"
            f"👨‍👩‍👧‍👦 Всего человек: {total_people} (взрослых: {booking.adults}, "
            f"детей: {booking.children}, из них {children_needing_beds} с местами)\n"
            f"💰 Сумма: {balance['total']}₽\n"
            f"✅ Внесено: {balance['paid']}₽\n"
            f"🧾 Осталось: {balance['remaining']}₽\n"
            f"💬 Комментарий: {booking.comment}\n"
            f"Статус: {status}"
        )
        buttons = [
            [
                InlineKeyboardButton(
                    text="💬 Связаться с администратором",
                    callback_data=f"user_reply_{booking.id}",
                )
            ]
        ]
        if booking.status in {BookingStatus.NEW.value, BookingStatus.PENDING.value}:
            buttons.insert(
                0,
                [
                    InlineKeyboardButton(
                        text="✏️ Изменить заявку",
                        callback_data=f"guest_edit_booking_{booking.id}",
                    )
                ],
            )
        if booking.status != "awaiting_cancellation":
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="❌ Отменить заявку",
                        callback_data=f"cancel_booking_{booking.id}",
                    )
                ]
            )
        if idx == len(bookings) - 1:
            buttons.append(
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
            )
        await callback.message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
            parse_mode="HTML",
        )

    logging.info("User %s viewed own bookings: count=%s", user_id, len(bookings))
    await callback.answer()


@router.callback_query(F.data.startswith("guest_edit_booking_"))
async def guest_edit_booking_start(callback: CallbackQuery, state: FSMContext):
    booking_id = int(callback.data.replace("guest_edit_booking_", "", 1))
    booking = session.query(Booking).filter_by(id=booking_id, user_id=callback.from_user.id).first()
    if not booking or booking.status not in {BookingStatus.NEW.value, BookingStatus.PENDING.value}:
        await callback.answer("Эту заявку уже нельзя редактировать.", show_alert=True)
        return
    await state.update_data(guest_edit_booking_id=booking.id)
    await state.set_state(BookingStates.waiting_for_guest_booking_edit)
    await callback.message.answer(
        f"✏️ Изменение заявки #{booking.id}\n\n"
        "Отправьте одной строкой:\n"
        "дата заезда; суток; взрослых; детей; детей с местом; ФИО; телефон; комментарий\n\n"
        "Пример:\n"
        "15.07.2026; 3; 2; 1; 1; Иванов Иван; +79001234567; Нужна парковка\n\n"
        f"Текущий вариант проживания останется: {room_type_names.get(booking.room_type, booking.room_type)}"
    )
    await callback.answer()


def _room_conflicts(booking, start_date, end_date) -> bool:
    requested_rooms = set(ROOM_DEPENDENCIES.get(booking.room_type, [booking.room_type]))
    others = (
        session.query(Booking)
        .filter(
            Booking.id != booking.id,
            Booking.status.in_(ACTIVE_BOOKING_STATUSES),
            Booking.date_from < end_date,
            Booking.date_to > start_date,
        )
        .all()
    )
    return any(
        requested_rooms.intersection(ROOM_DEPENDENCIES.get(other.room_type, [other.room_type]))
        for other in others
    )


@router.message(BookingStates.waiting_for_guest_booking_edit, F.text)
async def guest_edit_booking_save(message: Message, state: FSMContext):
    data = await state.get_data()
    booking = session.query(Booking).filter_by(
        id=data.get("guest_edit_booking_id"), user_id=message.from_user.id
    ).first()
    if not booking or booking.status not in {BookingStatus.NEW.value, BookingStatus.PENDING.value}:
        await state.clear()
        await message.answer("Эту заявку уже нельзя редактировать.")
        return
    try:
        parts = [part.strip() for part in message.text.split(";", 7)]
        if len(parts) != 8:
            raise ValueError("нужно заполнить все 8 полей через точку с запятой")
        start_date = datetime.strptime(parts[0], "%d.%m.%Y").date()
        duration, adults, children, children_with_beds = map(int, parts[1:5])
        if duration < 1 or adults < 1 or children < 0 or not 0 <= children_with_beds <= children:
            raise ValueError("проверьте количество суток и гостей")
        end_date = start_date.fromordinal(start_date.toordinal() + duration)
        options = await get_optimal_room_combinations(adults + children_with_beds, duration=duration)
        if booking.room_type not in options:
            raise ValueError("текущий вариант проживания не подходит для нового количества гостей")
        if _room_conflicts(booking, start_date, end_date):
            raise ValueError("выбранные даты уже заняты")
        before = json.dumps(
            {
                "date_from": booking.date_from.isoformat(),
                "date_to": booking.date_to.isoformat(),
                "adults": booking.adults,
                "children": booking.children,
                "full_name": booking.full_name,
                "phone": booking.phone,
                "comment": booking.comment,
            },
            ensure_ascii=False,
        )
        booking.date_from = start_date
        booking.date_to = end_date
        booking.adults = adults
        booking.children = children
        booking.children_beds = json.dumps([1] * children_with_beds + [0] * (children - children_with_beds))
        booking.full_name = parts[5]
        booking.phone = parts[6]
        booking.comment = parts[7]
        after = json.dumps(
            {
                "date_from": booking.date_from.isoformat(),
                "date_to": booking.date_to.isoformat(),
                "adults": booking.adults,
                "children": booking.children,
                "full_name": booking.full_name,
                "phone": booking.phone,
                "comment": booking.comment,
            },
            ensure_ascii=False,
        )
        session.add(BookingHistory(booking_id=booking.id, actor_id=message.from_user.id, action="Гость изменил заявку", before=before, after=after))
        session.commit()
        clear_booked_dates_cache()
        await state.clear()
        await message.answer(f"✅ Заявка #{booking.id} обновлена и снова ожидает проверки администратора.")
    except ValueError as error:
        await message.answer(f"Не удалось изменить заявку: {error}")
