from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.states import BookingStates
from config import ADMIN_CHAT_ID, BOOKING_STATUS_LABELS
from services.booking_card_service import build_booking_card_data, format_admin_booking_card
from services.booking_management_service import change_booking_stage, soft_delete_booking, update_booking_fields
from services.stay_service import STAY_STATUSES, set_stay_status

router = Router()

STAY_LABELS = {
    "awaiting_checkin": "🕒 Ожидает заезд",
    "checked_in": "🏡 Заселён",
    "checked_out": "🚗 Выехал",
    "completed": "✅ Завершён",
}

FIELD_LABELS = {
    "full_name": "👤 Имя",
    "phone": "📞 Телефон",
    "adults": "👥 Взрослые",
    "children": "🧒 Дети",
    "manual_total": "💰 Стоимость",
    "comment": "💬 Комментарий гостя",
    "admin_comment": "📝 Заметка администратора",
}

async def _require_admin(callback: CallbackQuery) -> bool:
    if str(callback.from_user.id) == str(ADMIN_CHAT_ID):
        return True
    await callback.answer("Нет прав", show_alert=True)
    return False


def admin_booking_control_keyboard(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✏️ Изменить данные", callback_data=f"admin_edit_menu_{booking_id}")],
        [InlineKeyboardButton(text="💳 Управление оплатой", callback_data=f"admin_add_payment_{booking_id}")],
        [InlineKeyboardButton(text="🏡 Управление проживанием", callback_data=f"admin_stay_status_{booking_id}")],
        [InlineKeyboardButton(text="📌 Этап обработки заявки", callback_data=f"admin_stage_menu_{booking_id}")],
        [InlineKeyboardButton(text="🗑 Удалить заявку", callback_data=f"admin_delete_booking_{booking_id}")],
    ])


async def show_booking_control(message: Message, booking_id: int) -> None:
    text = format_admin_booking_card(await build_booking_card_data(booking_id))
    await message.answer(text, reply_markup=admin_booking_control_keyboard(booking_id))


@router.callback_query(F.data.startswith("admin_edit_menu_"))
async def edit_booking_menu(callback: CallbackQuery):
    if not await _require_admin(callback):
        return
    booking_id = int(callback.data.rsplit("_", 1)[1])
    rows = [
        [InlineKeyboardButton(text=label, callback_data=f"admin_edit_field_{booking_id}_{field}")]
        for field, label in FIELD_LABELS.items()
    ]
    rows.append([InlineKeyboardButton(text="📅 Даты и размещение", callback_data=f"admin_edit_booking_{booking_id}")])
    await callback.message.answer(
        f"Что изменить в заявке #{booking_id}?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_edit_field_"))
async def edit_booking_field(callback: CallbackQuery, state: FSMContext):
    if not await _require_admin(callback):
        return
    raw = callback.data.replace("admin_edit_field_", "", 1)
    booking_id_text, field = raw.split("_", 1)
    await state.update_data(admin_edit_booking_id=int(booking_id_text), admin_edit_field=field)
    await state.set_state(BookingStates.waiting_for_admin_booking_field_value)
    await callback.message.answer(f"Введите новое значение поля «{FIELD_LABELS[field]}» для заявки #{booking_id_text}:")
    await callback.answer()


@router.message(BookingStates.waiting_for_admin_booking_field_value, F.text)
async def save_booking_field(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    data = await state.get_data()
    booking_id = int(data["admin_edit_booking_id"])
    field = data["admin_edit_field"]
    try:
        update_booking_fields(booking_id, message.from_user.id, **{field: message.text})
    except Exception as error:
        await message.answer(f"Не удалось изменить поле: {error}")
        return
    await state.clear()
    await message.answer("✅ Данные сохранены.")
    await show_booking_control(message, booking_id)


@router.callback_query(F.data.startswith("admin_stage_menu_"))
async def booking_stage_menu(callback: CallbackQuery):
    if not await _require_admin(callback):
        return
    booking_id = int(callback.data.rsplit("_", 1)[1])
    stages = ("new", "pending", "awaiting_payment", "paid", "awaiting_cancellation", "canceled", "rejected")
    rows = [[InlineKeyboardButton(text=BOOKING_STATUS_LABELS.get(stage, stage), callback_data=f"admin_set_stage_{booking_id}_{stage}")] for stage in stages]
    await callback.message.answer("Выберите этап обработки заявки:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_set_stage_"))
async def save_booking_stage(callback: CallbackQuery):
    if not await _require_admin(callback):
        return
    raw = callback.data.replace("admin_set_stage_", "", 1)
    booking_id_text, stage = raw.split("_", 1)
    change_booking_stage(int(booking_id_text), callback.from_user.id, stage)
    await callback.message.edit_text(f"✅ Этап заявки #{booking_id_text}: {BOOKING_STATUS_LABELS.get(stage, stage)}")
    await show_booking_control(callback.message, int(booking_id_text))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_delete_booking_"))
async def confirm_delete_booking(callback: CallbackQuery):
    if not await _require_admin(callback):
        return
    booking_id = int(callback.data.rsplit("_", 1)[1])
    await callback.message.answer(
        f"Удалить заявку #{booking_id}? Она исчезнет из списков и уведомлений, но историю можно будет восстановить.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"admin_confirm_delete_{booking_id}"),
            InlineKeyboardButton(text="Отмена", callback_data=f"admin_edit_menu_{booking_id}"),
        ]]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_confirm_delete_"))
async def delete_booking(callback: CallbackQuery):
    if not await _require_admin(callback):
        return
    booking_id = int(callback.data.rsplit("_", 1)[1])
    soft_delete_booking(booking_id, callback.from_user.id)
    await callback.message.edit_text(f"🗑 Заявка #{booking_id} удалена. Данные и история сохранены.")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_stay_status_"))
async def choose_stay_status(callback: CallbackQuery):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("Нет прав", show_alert=True)
        return
    booking_id = int(callback.data.rsplit("_", 1)[1])
    rows = [
        [InlineKeyboardButton(text=STAY_LABELS[status], callback_data=f"admin_set_stay_{booking_id}_{status}")]
        for status in STAY_STATUSES
    ]
    await callback.message.answer(
        f"Выберите статус проживания заявки #{booking_id}:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_set_stay_"))
async def save_stay_status(callback: CallbackQuery):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("Нет прав", show_alert=True)
        return
    raw = callback.data.replace("admin_set_stay_", "", 1)
    booking_id_text, status = raw.split("_", 1)
    set_stay_status(int(booking_id_text), status, callback.from_user.id, "Ручное изменение администратором")
    await callback.message.edit_text(f"Статус проживания заявки #{booking_id_text}: {STAY_LABELS[status]}")
    await callback.answer()
