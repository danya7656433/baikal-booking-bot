from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

ROOM_DEPENDENCIES = {
    "2": ["2", "2+4", "2+6", "all"],
    "4": ["4", "2+4", "4+6", "all"],
    "6": ["6", "2+6", "4+6", "all"],
    "5": ["5", "all"],
    "2+4": ["2", "4", "2+4", "all"],
    "2+6": ["2", "6", "2+6", "all"],
    "4+6": ["4", "6", "4+6", "all"],
    "all": ["2", "4", "6", "5", "2+4", "2+6", "4+6", "all"],
}


def build_room_switch_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="2", callback_data=f"room_2_{year}_{month}"),
                InlineKeyboardButton(text="4", callback_data=f"room_4_{year}_{month}"),
                InlineKeyboardButton(text="6", callback_data=f"room_6_{year}_{month}"),
                InlineKeyboardButton(text="Домик", callback_data=f"room_5_{year}_{month}"),
                InlineKeyboardButton(
                    text="Все 14", callback_data=f"room_all_{year}_{month}"
                ),
            ]
        ]
    )
