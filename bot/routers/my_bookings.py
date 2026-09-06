import logging
import json
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.states import BookingStates
from config import ACTIVE_BOOKING_STATUSES, BookingStatus, room_type_names
from database import Booking, BookingHistory, get_session
from services.booking_service import get_children_beds
from services.booking_card_service import build_booking_card_data, format_guest_booking_card
from services.financial_service import calculate_booking_balance, payment_status_label
from services.inventory_service import is_available
from utils import ROOM_DEPENDENCIES, calculate_revenue, clear_booked_dates_cache, get_optimal_room_combinations

router = Router()


@router.callback_query(F.data == "my_bookings")
async def my_bookings(callback: CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id
    with get_session() as db_session:
        bookings = (
            db_session.query(Booking)
            .filter(
                Booking.user_id == user_id,
                Booking.status.in_(ACTIVE_BOOKING_STATUSES),
                Booking.deleted_at.is_(None),
            )
            .order_by(Booking.created_at.asc())
            .all()
        )
        db_session.expunge_all()

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
        text = format_guest_booking_card(await build_booking_card_data(booking.id))
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
    with get_session() as db_session:
        booking = db_session.query(Booking).filter_by(id=booking_id, user_id=callback.from_user.id).first()
        if not booking or booking.status not in {BookingStatus.NEW.value, BookingStatus.PENDING.value}:
            await callback.answer("Эту заявку уже нельзя редактировать.", show_alert=True)
            return
        current_room = room_type_names.get(booking.room_type, booking.room_type)
    await state.update_data(guest_edit_booking_id=booking_id)
    await state.set_state(BookingStates.waiting_for_guest_booking_edit)
    await callback.message.answer(
        f"✏️ Изменение заявки #{booking.id}\n\n"
        "Отправьте одной строкой:\n"
        "дата заезда; суток; взрослых; детей; детей с местом; ФИО; телефон; комментарий\n\n"
        "Пример:\n"
        "15.07.2026; 3; 2; 1; 1; Иванов Иван; +79001234567; Нужна парковка\n\n"
        f"Текущий вариант проживания останется: {current_room}"
    )
    await callback.answer()


def _room_conflicts(db_session, booking, start_date, end_date) -> bool:
    return not is_available(db_session, booking.room_type, start_date, end_date, booking.id)


@router.message(BookingStates.waiting_for_guest_booking_edit, F.text)
async def guest_edit_booking_save(message: Message, state: FSMContext):
    data = await state.get_data()
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
        with get_session() as db_session:
            booking = db_session.query(Booking).filter_by(
                id=data.get("guest_edit_booking_id"), user_id=message.from_user.id
            ).first()
            if not booking or booking.status not in {BookingStatus.NEW.value, BookingStatus.PENDING.value}:
                raise ValueError("эту заявку уже нельзя редактировать")
            if booking.room_type not in options:
                raise ValueError("текущий вариант проживания не подходит для нового количества гостей")
            if _room_conflicts(db_session, booking, start_date, end_date):
                raise ValueError("выбранные даты уже заняты")
            before = json.dumps(
                {"date_from": booking.date_from.isoformat(), "date_to": booking.date_to.isoformat(), "adults": booking.adults, "children": booking.children, "full_name": booking.full_name, "phone": booking.phone, "comment": booking.comment},
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
                {"date_from": booking.date_from.isoformat(), "date_to": booking.date_to.isoformat(), "adults": booking.adults, "children": booking.children, "full_name": booking.full_name, "phone": booking.phone, "comment": booking.comment},
                ensure_ascii=False,
            )
            booking_id = booking.id
            db_session.add(BookingHistory(booking_id=booking.id, actor_id=message.from_user.id, action="Гость изменил заявку", before=before, after=after))
        clear_booked_dates_cache()
        await state.clear()
        await message.answer(f"✅ Заявка #{booking_id} обновлена и снова ожидает проверки администратора.")
    except ValueError as error:
        await message.answer(f"Не удалось изменить заявку: {error}")
