import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.keyboards import admin_payment_confirm_keyboard, admin_payment_method_keyboard, admin_menu_keyboard
from bot.states import BookingStates
from config import ADMIN_CHAT_ID
from db import Booking, get_session
from services.payment_service import add_payment, preview_payment

router = Router()


def _is_admin(user_id: int) -> bool:
    return str(user_id) == str(ADMIN_CHAT_ID)


@router.callback_query(F.data.startswith("admin_add_payment_"))
async def start_add_payment(callback: CallbackQuery, state: FSMContext):
    if not _is_admin(callback.from_user.id):
        await callback.answer("Нет прав", show_alert=True)
        return
    booking_id = int(callback.data.rsplit("_", 1)[1])
    with get_session() as db_session:
        booking = db_session.get(Booking, booking_id)
        if booking is None:
            await callback.answer("Заявка не найдена", show_alert=True)
            return
    await state.update_data(add_payment_booking_id=booking_id)
    await state.set_state(BookingStates.waiting_for_payment_amount)
    await callback.message.answer(f"➕ Доплата по заявке #{booking_id}\n\nВведите полученную сумму:")
    await callback.answer()


@router.message(BookingStates.waiting_for_payment_amount, F.text)
async def receive_payment_amount(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    numbers = re.findall(r"\d+", message.text or "")
    if not numbers or int(numbers[0]) <= 0:
        await message.answer("Введите сумму числом. Например: 5000")
        return
    await state.update_data(add_payment_amount=int(numbers[0]))
    await state.set_state(BookingStates.waiting_for_payment_method)
    await message.answer("Выберите способ оплаты:", reply_markup=admin_payment_method_keyboard())


@router.message(BookingStates.waiting_for_payment_method, F.text)
async def receive_payment_method(message: Message, state: FSMContext):
    if not _is_admin(message.from_user.id):
        return
    methods = {"💳 Перевод": "перевод", "💵 Наличные": "наличные", "🧾 Другое": "другое"}
    method = methods.get(message.text)
    if method is None:
        await message.answer("Выберите способ оплаты кнопкой снизу.", reply_markup=admin_payment_method_keyboard())
        return
    data = await state.get_data()
    booking_id = int(data["add_payment_booking_id"])
    amount = int(data["add_payment_amount"])
    with get_session() as db_session:
        preview = preview_payment(db_session, booking_id, amount)
    await state.update_data(add_payment_method=method)
    if preview["overpaid"] > 0:
        await state.set_state(BookingStates.waiting_for_overpayment_confirmation)
        await message.answer(
            f"⚠️ После оплаты возникнет переплата {preview['overpaid']}₽.\nПодтвердить?",
            reply_markup=admin_payment_confirm_keyboard(),
        )
        return
    await _save_payment(message, state)


@router.message(BookingStates.waiting_for_overpayment_confirmation, F.text == "⚠️ Подтвердить переплату")
async def confirm_overpayment(message: Message, state: FSMContext):
    if _is_admin(message.from_user.id):
        await _save_payment(message, state)


@router.message(BookingStates.waiting_for_overpayment_confirmation, F.text == "❌ Отмена")
async def cancel_overpayment(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Добавление оплаты отменено.", reply_markup=admin_menu_keyboard())


async def _save_payment(message: Message, state: FSMContext):
    data = await state.get_data()
    booking_id = int(data["add_payment_booking_id"])
    balance = add_payment(
        booking_id,
        int(data["add_payment_amount"]),
        data["add_payment_method"],
        message.from_user.id,
    )
    with get_session() as db_session:
        booking = db_session.get(Booking, booking_id)
        user_id = booking.user_id if booking else None
        if booking and balance["net_paid"] > 0:
            booking.status = "paid"
            booking.payment_deadline = None
    await state.clear()
    text = (
        f"✅ Оплата по заявке #{booking_id} добавлена.\n"
        f"Внесено: {balance['paid']}₽\n"
        f"Осталось: {balance['remaining']}₽"
    )
    await message.answer(text, reply_markup=admin_menu_keyboard())
    if user_id:
        await message.bot.send_message(user_id, f"💳 {text}")
