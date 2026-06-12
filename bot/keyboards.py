from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)


BTN_BOOK = "📅 Забронировать номер"
BTN_AVAILABILITY = "🔎 Свободные даты"
BTN_MY_BOOKINGS = "📋 Мои заявки"
BTN_REVIEWS = "⭐ Посмотреть отзывы"
BTN_SUPPORT = "📞 Поддержка"
BTN_ABOUT = "ℹ️ О нас"
BTN_ADMIN = "👨‍💼 Админ-панель"
BTN_MAIN_MENU = "🏠 Главное меню"
BTN_BACK = "⬅️ Назад"
BTN_CHANGE_GUESTS = "👥 Изменить гостей"
BTN_BACK_ROOMS = "⬅️ К выбору номера"

BTN_RULES_LIVING = "📜 Правила проживания"
BTN_TERRITORY = "🌳 Обзор территории"
BTN_BOOKING_RULES = "📅 Правила бронирования"
BTN_CANCELLATION_RULES = "❌ Правила отмены"
BTN_ROOMS_DESCRIPTION = "🏠 Обзор комнат"
BTN_FAQ = "❓ Частые вопросы"
BTN_PREV_PHOTO = "⬅️ Фото"
BTN_NEXT_PHOTO = "Фото ➡️"
BTN_PREV_ROOM = "⬅️ Номер"
BTN_NEXT_ROOM = "Номер ➡️"

BTN_ADMIN_BOOKINGS = "📋 Активные заявки"
BTN_ADMIN_CREATE_BOOKING = "➕ Создать заявку"
BTN_ADMIN_SUPPORT = "💬 Обращения"
BTN_ADMIN_CALENDAR = "📅 Календарь"
BTN_ADMIN_STATS = "📊 Статистика"
BTN_ADMIN_PRICES = "📈 Цены"
BTN_ADMIN_REVIEWS = "⭐ Отзывы"
BTN_ADMIN_NEWS = "📰 Новость"
BTN_ADMIN_DB = "💾 База"
BTN_ADMIN_LOG = "📄 Лог"
BTN_ADMIN_YEAR = "📅 Год"
BTN_ADMIN_SEASON = "🌞 Сезон"
BTN_ADMIN_USERS = "👥 Пользователи"
BTN_ADMIN_PAYMENT = "💳 Оплата заявки"
BTN_ADMIN_FINANCE = "💰 Финансы"
BTN_ADMIN_EXPORTS = "📤 Экспорт"
BTN_ADMIN_BACKUPS = "🗄 Бэкапы"
BTN_ADMIN_HEALTH = "🩺 Состояние бота"
BTN_ADMIN_RESTART = "🔄 Перезапустить бота"


def main_menu_keyboard(is_admin: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text=BTN_BOOK)],
        [KeyboardButton(text=BTN_AVAILABILITY)],
        [KeyboardButton(text=BTN_MY_BOOKINGS), KeyboardButton(text=BTN_REVIEWS)],
        [KeyboardButton(text=BTN_SUPPORT), KeyboardButton(text=BTN_ABOUT)],
    ]
    if is_admin:
        keyboard.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def compact_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_MAIN_MENU)]],
        resize_keyboard=True,
    )


def about_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_RULES_LIVING), KeyboardButton(text=BTN_TERRITORY)],
            [KeyboardButton(text=BTN_BOOKING_RULES), KeyboardButton(text=BTN_CANCELLATION_RULES)],
            [KeyboardButton(text=BTN_ROOMS_DESCRIPTION)],
            [KeyboardButton(text=BTN_FAQ)],
            [KeyboardButton(text=BTN_BACK)],
        ],
        resize_keyboard=True,
    )


def about_gallery_keyboard(include_rooms: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=BTN_PREV_PHOTO), KeyboardButton(text=BTN_NEXT_PHOTO)]]
    if include_rooms:
        keyboard.append([KeyboardButton(text=BTN_PREV_ROOM), KeyboardButton(text=BTN_NEXT_ROOM)])
    keyboard.append([KeyboardButton(text=BTN_ROOMS_DESCRIPTION), KeyboardButton(text=BTN_TERRITORY)])
    keyboard.append([KeyboardButton(text=BTN_BACK)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def admin_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_ADMIN_BOOKINGS), KeyboardButton(text=BTN_ADMIN_CREATE_BOOKING)],
            [KeyboardButton(text=BTN_ADMIN_SUPPORT), KeyboardButton(text=BTN_ADMIN_CALENDAR)],
            [KeyboardButton(text=BTN_ADMIN_STATS), KeyboardButton(text=BTN_ADMIN_PRICES)],
            [KeyboardButton(text=BTN_ADMIN_REVIEWS), KeyboardButton(text=BTN_ADMIN_NEWS)],
            [KeyboardButton(text=BTN_ADMIN_DB), KeyboardButton(text=BTN_ADMIN_LOG)],
            [KeyboardButton(text=BTN_ADMIN_YEAR), KeyboardButton(text=BTN_ADMIN_SEASON)],
            [KeyboardButton(text=BTN_ADMIN_USERS), KeyboardButton(text=BTN_ADMIN_PAYMENT)],
            [KeyboardButton(text=BTN_ADMIN_FINANCE), KeyboardButton(text=BTN_ADMIN_EXPORTS)],
            [KeyboardButton(text=BTN_ADMIN_BACKUPS), KeyboardButton(text=BTN_ADMIN_HEALTH)],
            [KeyboardButton(text=BTN_ADMIN_RESTART)],
            [KeyboardButton(text=BTN_BACK)],
        ],
        resize_keyboard=True,
    )


def availability_people_keyboard() -> ReplyKeyboardMarkup:
    return compact_main_keyboard()


def availability_rooms_keyboard(room_types: list[str], room_names: dict[str, str]) -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text=room_names.get(room_type, room_type))] for room_type in room_types]
    keyboard.append([KeyboardButton(text=BTN_CHANGE_GUESTS), KeyboardButton(text=BTN_MAIN_MENU)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def availability_calendar_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_BACK_ROOMS)],
            [KeyboardButton(text=BTN_CHANGE_GUESTS), KeyboardButton(text=BTN_MAIN_MENU)],
        ],
        resize_keyboard=True,
    )


def contact_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📱 Отправить контакт", request_contact=True)],
            [KeyboardButton(text="✏️ Ввести вручную")],
            [KeyboardButton(text=BTN_MAIN_MENU)],
        ],
        resize_keyboard=True,
    )


def admin_booking_actions(booking_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить", callback_data=f"approve_booking_{booking_id}"
                ),
                InlineKeyboardButton(
                    text="❌ Отклонить", callback_data=f"reject_booking_{booking_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💬 Комментарий", callback_data=f"admin_comment_{booking_id}"
                )
            ],
        ]
    )


def admin_payment_method_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="💳 Перевод"), KeyboardButton(text="💵 Наличные")],
            [KeyboardButton(text="🧾 Другое")],
        ],
        resize_keyboard=True,
    )


def admin_payment_confirm_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="⚠️ Подтвердить переплату")],
            [KeyboardButton(text="❌ Отмена")],
        ],
        resize_keyboard=True,
    )
