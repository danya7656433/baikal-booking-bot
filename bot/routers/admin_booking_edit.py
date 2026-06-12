from aiogram import F, Router
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup

from config import ADMIN_CHAT_ID
from services.stay_service import STAY_STATUSES, set_stay_status

router = Router()

STAY_LABELS = {
    "awaiting_checkin": "🕒 Ожидает заезд",
    "checked_in": "🏡 Заселён",
    "checked_out": "🚗 Выехал",
    "completed": "✅ Завершён",
}


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
