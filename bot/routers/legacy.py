import os
import re
import sys
import json
import shutil
import ast
from datetime import datetime, timedelta


from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command, StateFilter
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
    FSInputFile,
    InputMediaPhoto,
    ForceReply,
)
from aiogram.fsm.context import FSMContext
from aiogram import Router
from datetime import datetime, timedelta, date
from database import (
    session,
    Room,
    User,
    Booking,
    Review,
    AdminLog,
    UpdateLog,
    SupportMessage,
    SupportTicket,
    RoomPriceOverride,
    BookingYear,
    BookingSeason,
)
from utils import (
    get_booked_dates,
    backup_database,
    get_optimal_room_combinations,
    is_room_available,
    calculate_revenue,
    clear_booked_dates_cache,
)
from config import (
    ACTIVE_BOOKING_STATUSES,
    BOOKING_STATUS_LABELS,
    BookingStatus,
    room_type_names,
    PRICE_PER_ADULT,
    ALL_ROOMS_PRICE,
    HOTEL_ADDRESS,
    ADMIN_CONTACT,
    LOCATION_LINK,
    ADMIN_CHAT_ID,
    ADMIN_USERNAME,
    PHOTO_STORAGE_CHAT_ID,
    PAYMENT_DEADLINE_HOURS,
)
from bot.middleware import RateLimitMiddleware
from bot.routers.admin import router as admin_router
from bot.routers.admin_prices import router as admin_prices_router
from bot.routers.admin_news import router as admin_news_router
from bot.routers.my_bookings import router as my_bookings_router
from bot.routers.my_bookings import my_bookings as show_my_bookings
from bot.keyboards import (
    BTN_ABOUT,
    BTN_ADMIN,
    BTN_ADMIN_BOOKINGS,
    BTN_ADMIN_CALENDAR,
    BTN_ADMIN_CREATE_BOOKING,
    BTN_ADMIN_DB,
    BTN_ADMIN_LOG,
    BTN_ADMIN_NEWS,
    BTN_ADMIN_PAYMENT,
    BTN_ADMIN_PRICES,
    BTN_ADMIN_REVIEWS,
    BTN_ADMIN_SEASON,
    BTN_ADMIN_STATS,
    BTN_ADMIN_SUPPORT,
    BTN_ADMIN_USERS,
    BTN_ADMIN_YEAR,
    BTN_AVAILABILITY,
    BTN_BACK,
    BTN_BACK_ROOMS,
    BTN_BOOK,
    BTN_BOOKING_RULES,
    BTN_CANCELLATION_RULES,
    BTN_CHANGE_GUESTS,
    BTN_FAQ,
    BTN_MAIN_MENU,
    BTN_MY_BOOKINGS,
    BTN_NEXT_PHOTO,
    BTN_NEXT_ROOM,
    BTN_PREV_PHOTO,
    BTN_PREV_ROOM,
    BTN_ROOMS_DESCRIPTION,
    BTN_REVIEWS,
    BTN_RULES_LIVING,
    BTN_SUPPORT,
    BTN_TERRITORY,
    admin_menu_keyboard,
    about_gallery_keyboard,
    about_menu_keyboard,
    availability_calendar_keyboard,
    availability_people_keyboard,
    availability_rooms_keyboard,
    compact_main_keyboard,
    main_menu_keyboard,
)
from bot.states import BookingStates
from services.availability_service import get_available_room_options, is_available_for_duration
from services.booking_service import get_children_beds, get_duration_text
from services.financial_service import apply_confirmed_payment, calculate_booking_balance, payment_status_label
from services.paths import BACKUP_DIR, DATABASE_PATH, LOG_PATH
from services.draft_service import clear_booking_draft, get_booking_draft, save_booking_draft
from utils import ROOM_DEPENDENCIES
from utils import get_optimal_rooms
import asyncio
import logging, traceback
from calendar import monthcalendar, month_name
from typing import Tuple, List, Union
from logging.handlers import RotatingFileHandler
from aiogram.exceptions import TelegramBadRequest
from asyncio import Lock

handler = RotatingFileHandler(
    filename=LOG_PATH,
    maxBytes=10 * 1024 * 1024,  # 10 МБ
    backupCount=5,
    encoding="utf-8",  # Явно указываем UTF-8
)
handler.setFormatter(
    logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
)

logging.basicConfig(
    handlers=[handler],
    level=logging.INFO,  # Используем INFO для минимизации логов
    encoding="utf-8",  # Указываем UTF-8 для совместимости
)

router = Router()


class MessageBackedCallback:
    def __init__(self, message: Message, data: str | None = None):
        self.from_user = message.from_user
        self.message = MessageEditAdapter(message)
        self.bot = message.bot
        self.data = data

    async def answer(self, *args, **kwargs):
        return None


class MessageEditAdapter:
    def __init__(self, message: Message):
        self._message = message
        self.text = message.text
        self.reply_markup = None
        self.chat = message.chat
        self.bot = message.bot
        self.caption = getattr(message, "caption", "") or ""
        self.photo = getattr(message, "photo", None)

    async def edit_text(self, text: str, **kwargs):
        return await self._message.answer(text, **kwargs)

    async def answer(self, text: str, **kwargs):
        return await self._message.answer(text, **kwargs)

    async def answer_photo(self, *args, **kwargs):
        return await self._message.answer_photo(*args, **kwargs)

    async def answer_document(self, *args, **kwargs):
        return await self._message.answer_document(*args, **kwargs)

    async def edit_media(self, media, **kwargs):
        if isinstance(media, InputMediaPhoto):
            return await self._message.answer_photo(
                photo=media.media,
                caption=media.caption,
                parse_mode=None,
                reply_markup=kwargs.get("reply_markup"),
            )
        return await self._message.answer("Медиа недоступно для предпросмотра.")

    async def edit_caption(self, caption: str, **kwargs):
        return await self._message.answer(caption, **kwargs)

    async def delete(self):
        try:
            await self._message.delete()
        except TelegramBadRequest:
            pass


async def send_reply_keyboard(message: Message, reply_markup: ReplyKeyboardMarkup):
    sent = await message.answer("\u2060", reply_markup=reply_markup)
    try:
        await sent.delete()
    except TelegramBadRequest:
        pass


async def show_reply_keyboard_message(
    message: Message,
    text: str,
    reply_markup: ReplyKeyboardMarkup,
):
    return await message.answer(text, reply_markup=reply_markup)


async def show_gallery_controls(
    message: Message,
    state: FSMContext,
    include_rooms: bool,
):
    data = await state.get_data()
    old_id = data.get("gallery_controls_message_id")
    if old_id:
        try:
            await message.bot.delete_message(message.chat.id, old_id)
        except TelegramBadRequest:
            pass
    sent = await message.answer(
        "Управление обзором:",
        reply_markup=about_gallery_keyboard(include_rooms=include_rooms),
    )
    await state.update_data(gallery_controls_message_id=sent.message_id)
    return sent


async def show_main_menu_message(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "🌊 Главное меню системы бронирования Дача на Байкале 🌲",
        reply_markup=main_menu_keyboard(str(message.from_user.id) == str(ADMIN_CHAT_ID)),
    )


async def get_person_day_price_text(
    room_type: str,
    total_people: int,
    date_from: date,
    date_to: date,
) -> str:
    if total_people <= 0:
        return ""

    prices = []
    current_date = date_from
    with session as db_session:
        while current_date < date_to:
            if room_type == "all" and 8 <= total_people <= 10:
                prices.append(ALL_ROOMS_PRICE // total_people)
            else:
                override = (
                    db_session.query(RoomPriceOverride)
                    .filter_by(date=current_date)
                    .first()
                )
                prices.append(override.price if override else PRICE_PER_ADULT)
            current_date += timedelta(days=1)

    if not prices:
        return ""
    min_price = min(prices)
    max_price = max(prices)
    if min_price == max_price:
        return f"💰 За человека в сутки: {min_price}₽\n"
    return f"💰 За человека в сутки: от {min_price}₽ до {max_price}₽\n"


@router.message(F.text == BTN_MAIN_MENU)
async def handle_global_main_menu(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        message_id = data.get("availability_message_id")
        if message_id:
            try:
                await message.bot.delete_message(message.chat.id, message_id)
            except TelegramBadRequest:
                pass
    finally:
        await show_main_menu_message(message, state)


@router.message(F.text == BTN_BACK)
async def handle_global_back_to_main(message: Message, state: FSMContext):
    await show_main_menu_message(message, state)


@router.message(StateFilter("*"), F.text.in_({BTN_MAIN_MENU, BTN_BACK, BTN_ADMIN}))
async def handle_global_navigation_from_any_state(message: Message, state: FSMContext):
    text = message.text.strip()
    if text == BTN_ADMIN:
        if str(message.from_user.id) != str(ADMIN_CHAT_ID):
            await message.answer("У вас нет доступа к админ-панели.")
            return
        await admin_panel(message, state)
        return
    await show_main_menu_message(message, state)


@router.callback_query(F.data.regexp(r"show_bookings_(\d+)_(\d+)_(\d+)_(.+)"))
async def show_bookings_for_date(callback: CallbackQuery, state: FSMContext):
    from config import room_type_names
    from utils import format_booking_info

    logging.debug(f"Вызов show_bookings_for_date: callback_data={callback.data}")
    try:
        # Извлекаем дату и room_type из callback_data
        parts = callback.data.split("_")
        year, month, day, room_type = (
            int(parts[2]),
            int(parts[3]),
            int(parts[4]),
            parts[5],
        )
        selected_date = datetime(year, month, day).date()

        # Сохраняем room_type в состояние
        await state.update_data(room_type=room_type)
        logging.debug(f"Извлечённый room_type: {room_type}, дата: {selected_date}")

        # Запрашиваем заявки, которые включают выбранную дату
        with session as db_session:
            bookings = (
                db_session.query(Booking)
                .filter(
                    Booking.status.in_(ACTIVE_BOOKING_STATUSES),
                    Booking.date_from <= selected_date,
                    Booking.date_to > selected_date,
                )
                .all()
            )

            # Фильтруем заявки по типу номера
            bookings = [
                b for b in bookings if room_type in ROOM_DEPENDENCIES[b.room_type]
            ]

            if not bookings:
                try:
                    await callback.message.edit_text(
                        f"📅 На {selected_date.strftime('%d.%m.%Y')} нет заявок для номера {room_type_names.get(room_type, room_type)}.",
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[
                                [
                                    InlineKeyboardButton(
                                        text="⬅️ Назад",
                                        callback_data=f"month_{year}_{month}_{room_type}",
                                    )
                                ]
                            ]
                        ),
                    )
                except TelegramBadRequest as e:
                    if "query is too old" in str(e):
                        await callback.message.delete()
                        await callback.message.answer(
                            f"📅 На {selected_date.strftime('%d.%m.%Y')} нет заявок для номера {room_type_names.get(room_type, room_type)}.",
                            reply_markup=InlineKeyboardMarkup(
                                inline_keyboard=[
                                    [
                                        InlineKeyboardButton(
                                            text="⬅️ Назад",
                                            callback_data=f"month_{year}_{month}_{room_type}",
                                        )
                                    ]
                                ]
                            ),
                        )
                    else:
                        raise
                await callback.answer()
                return

            # Словарь для преобразования статуса
            status_mapping = {
                "new": "Новая",
                "pending": "В обработке",
                "awaiting_payment": "Ожидает оплаты",
                "paid": "Оплачена",
                "cancelled": "Отменена",
            }

            # Формируем список заявок
            bookings_text = f"📅 Заявки на {selected_date.strftime('%d.%m.%Y')} для номера {room_type_names.get(room_type, room_type)}:\n\n"
            for booking in bookings:
                status_text = status_mapping.get(booking.status, booking.status)
                bookings_text += (
                    f"📌 Заявка #{booking.id}\n"
                    f"👤 Клиент: {booking.full_name} (@{booking.username if booking.username else 'без ника'})\n"
                    f"📞 Телефон: {booking.phone}\n"
                    f"{await format_booking_info(booking)}\n"
                    f"💬 Статус: {status_text}\n"
                    f"---\n"
                )

            # Кнопка "Назад"
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад",
                            callback_data=f"month_{year}_{month}_{room_type}",
                        )
                    ]
                ]
            )

            try:
                await callback.message.edit_text(bookings_text, reply_markup=kb)
            except TelegramBadRequest as e:
                if "query is too old" in str(e):
                    await callback.message.delete()
                    await callback.message.answer(bookings_text, reply_markup=kb)
                else:
                    raise
            await callback.answer()
    except Exception as e:
        logging.error(
            f"Ошибка в show_bookings_for_date: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer(
            "Произошла ошибка при отображении заявок.", show_alert=True
        )


photo_file_id_cache = {}


router.message.middleware(RateLimitMiddleware())
router.callback_query.middleware(RateLimitMiddleware())


def init_rooms():
    """
    Инициализирует комнаты в базе данных, если их еще нет.
    """
    try:
        # Проверяем, что сессия активна
        if session is None:
            raise ValueError("Сессия базы данных не инициализирована")

        # Проверяем, есть ли уже комнаты в базе
        if not session.query(Room).first():
            rooms = [
                Room(
                    name="2-местный",
                    capacity=2,
                    price=3000,
                    description="✔️Двуспальная кровать",
                    photo_id=None,
                ),
                Room(
                    name="4-местный",
                    capacity=4,
                    price=6000,
                    description="Номер на 4 человека с выходом на балкон и мансарду.\n"
                    "✔️Двуспальная кровать\n"
                    "✔️2 односпальные кровати",
                    photo_id=None,
                ),
                Room(
                    name="6-местный",
                    capacity=6,
                    price=9000,
                    description="✔️Двуспальная кровать\n" "✔️4 односпальных места",
                    photo_id=None,
                ),
                Room(
                    name="Домик на 2 места",
                    capacity=2,
                    price=3000,
                    description="Отдельный домик на 2 человека. Фото добавим в отдельную галерею.",
                    photo_id=None,
                ),
            ]
            session.add_all(rooms)
            session.commit()
            logging.info("Комнаты успешно инициализированы")
        else:
            logging.debug("Комнаты уже существуют в базе данных")
            if not session.query(Room).filter_by(name="Домик на 2 места").first():
                session.add(
                    Room(
                        name="Домик на 2 места",
                        capacity=2,
                        price=3000,
                        description="Отдельный домик на 2 человека. Фото добавим в отдельную галерею.",
                        photo_id=None,
                    )
                )
                session.commit()
                logging.info("Домик на 2 места добавлен в список комнат")
    except Exception as e:
        logging.error(f"Ошибка при инициализации комнат: {str(e)}")
        session.rollback()
        raise


async def get_file_id(bot: Bot, file_path: str) -> str:
    if file_path in photo_file_id_cache:
        logging.debug(f"Использован кэшированный file_id для {file_path}")
        return photo_file_id_cache[file_path]

    try:
        photo = FSInputFile(file_path)
        # Отправляем фото в чат для хранения фотографий
        message = await bot.send_photo(chat_id=PHOTO_STORAGE_CHAT_ID, photo=photo)
        file_id = message.photo[-1].file_id
        photo_file_id_cache[file_path] = file_id
        logging.info(f"Кэширован file_id для {file_path}: {file_id}")
        # Удаляем временное сообщение
        await message.delete()
        return file_id
    except Exception as e:
        logging.warning(f"Служебный чат фото недоступен для {file_path}: {str(e)}")
        raise


async def get_file_id_with_fallback(
    bot: Bot, file_path: str, fallback_chat_id: int | None = None
) -> str:
    return await get_file_id(bot, file_path)


ALLOWED_EXTENSIONS = {".py", ".db", ".env"}


def validate_python_file(file_path: str) -> bool:
    """Проверяет синтаксис Python-файла."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            ast.parse(f.read())
        return True
    except (SyntaxError, UnicodeDecodeError) as e:
        logging.error(f"Ошибка синтаксиса в файле {file_path}: {str(e)}")
        return False


def backup_file(file_path: str) -> str:
    """Создаёт резервную копию файла."""
    backup_dir = str(BACKUP_DIR / "files")
    os.makedirs(backup_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(
        backup_dir,
        f"{os.path.basename(file_path)}_{timestamp}{os.path.splitext(file_path)[1]}",
    )
    shutil.copy(file_path, backup_path)
    return backup_path


@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    from database import session, User

    init_rooms()
    user_id = message.from_user.id

    # Проверяем, есть ли пользователь в базе
    user = session.query(User).filter_by(user_id=user_id).first()
    if not user:
        # Создаём нового пользователя
        user = User(
            user_id=user_id, username=message.from_user.username, rules_accepted=False
        )
        session.add(user)
        session.commit()
        logging.info(
            f"Создан новый пользователь {user_id}, username=@{message.from_user.username or 'без ника'}"
        )

    # Если пользователь не ознакомился с правилами
    if not user.rules_accepted:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📜 Ознакомиться с правилами", callback_data="show_rules"
                    )
                ]
            ]
        )
        await message.answer(
            "🌊 **Добро пожаловать в систему бронирования Дача на Байкале! 🌲**\n\n"
            "Мы рады помочь вам спланировать незабываемый отдых на берегу Байкала! "
            "Бронируйте уютные номера, проверяйте свободные даты и получайте поддержку в любое время. "
            "Для начала, пожалуйста, ознакомьтесь с правилами бронирования и отмены.",
            parse_mode="Markdown",
            reply_markup=kb,
        )
        await state.set_state(BookingStates.waiting_for_rules_prompt)
        logging.info(
            f"Пользователь {user_id} направлен на ознакомление с правилами, состояние waiting_for_rules_prompt установлено"
        )
    else:
        await state.clear()
        await message.answer(
            "🌊 Главное меню системы бронирования Дача на Байкале 🌲",
            reply_markup=main_menu_keyboard(str(user_id) == str(ADMIN_CHAT_ID)),
        )
        logging.info(f"Пользователь {user_id} открыл главное меню, состояние сброшено")


async def preload_photo_cache(bot: Bot):
    photo_dir = "photos/territory"
    if os.path.exists(photo_dir):
        photos = [
            os.path.join(photo_dir, f)
            for f in os.listdir(photo_dir)
            if f.lower().endswith((".jpg", ".png"))
        ]
        for photo_path in photos:
            if photo_path not in photo_file_id_cache:
                try:
                    file_id = await get_file_id_with_fallback(bot, photo_path)
                    logging.info(f"Preloaded file_id for {photo_path}: {file_id}")
                except Exception as e:
                    logging.error(
                        f"Failed to preload file_id for {photo_path}: {str(e)}"
                    )


async def on_startup(bot: Bot):
    await preload_photo_cache(bot)
    # Инициализация BookingYear
    with session as db_session:
        booking_year = db_session.query(BookingYear).first()
        if not booking_year:
            db_session.add(BookingYear(year=2026))  # Устанавливаем 2026 по умолчанию
            db_session.commit()
            logging.info("Инициализирован BookingYear с годом 2026")
        else:
            logging.info(f"BookingYear уже существует: год {booking_year.year}")


@router.callback_query(F.data == "show_rules")
async def show_rules(callback: CallbackQuery, state: FSMContext):
    # Устанавливаем состояние для подтверждения правил
    await state.set_state(BookingStates.waiting_for_rules_agreement)

    rules_text = (
        "🌊 **Добро пожаловать в систему бронирования Дача на Байкале! 🌲**\n\n"
        "Мы рады помочь вам спланировать незабываемый отдых на берегу Байкала! "
        "Бронируйте уютные номера, проверяйте свободные даты и получайте поддержку в любое время. "
        "Для начала, пожалуйста, ознакомьтесь с правилами бронирования и отмены.\n\n"
        "📅 Правила бронирования (пошагово)\n\n"
        "1. Выберите дату заезда и количество суток через бота.\n"
        "2. Укажите количество взрослых и детей (до 3 лет — бесплатно).\n"
        "3. Выберите подходящий номер из доступных.\n"
        "4. Подтвердите заявку и отправьте свои контактные данные.\n"
        "5. Дождитесь подтверждения от администратора (в течение 24 часов).\n"
        "6. После подтверждения оплатите бронирование в течение 3 часов.\n"
        "7. Получите уведомление о бронировании с деталями заезда.\n\n"
        "ℹ️ Важно:\n"
        "- Бронирование доступно минимум за 1 день до заезда.\n"
        "- Максимальный срок бронирования — 31 день.\n"
        "- Для групп более 14 человек свяжитесь с администратором.\n\n"
        "❌ Правила отмены бронирования (пошагово)\n\n"
        "1. Перейдите в раздел 'Мои заявки' и выберите нужную заявку.\n"
        "2. Нажмите 'Отменить заявку' и укажите причину отмены.\n"
        "3. Дождитесь рассмотрения запроса администратором (в течение 24 часов).\n"
        "4. Получите уведомление о подтверждении или отклонения отмены.\n"
        "5. При подтверждении возврат средств будет выполнен в течение 1 рабочего дня.\n\n"
        "ℹ️ Важно:\n"
        "- Отмену с возвратом оплаты можно сделать не ранее чем за 7 дней до даты заезда.\n"
        "- При отмене менее чем за 7 дней удерживается 50% стоимости.\n"
        "- Все наши номера находятся на 2м этаже.\n\n"
        "✅ **При нажатии кнопки 'Ознакомлен' вы подтверждаете, что принимаете наши правила и полностью с ними ознакомлены.**"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Ознакомлен", callback_data="rules_accepted"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_start")],
        ]
    )
    await callback.message.edit_text(rules_text, parse_mode="Markdown", reply_markup=kb)
    current_state = await state.get_state()
    logging.info(
        f"Пользователь {callback.from_user.id} просмотрел правила, состояние: {current_state}"
    )
    await callback.answer()


@router.message(F.text, BookingStates.waiting_for_rules_agreement)
async def handle_text_during_rules_agreement(message: Message, state: FSMContext):
    rules_text = (
        "🌊 **Добро пожаловать в систему бронирования Дача на Байкале! 🌲**\n\n"
        "Мы рады помочь вам спланировать незабываемый отдых на берегу Байкала! "
        "Бронируйте уютные номера, проверяйте свободные даты и получайте поддержку в любое время. "
        "Для начала, пожалуйста, ознакомьтесь с правилами бронирования и отмены.\n\n"
        "📅 **Правила бронирования (пошагово)**\n\n"
        "1. Выберите дату заезда и количество суток через бота.\n"
        "2. Укажите количество взрослых и детей (до 3 лет — бесплатно).\n"
        "3. Выберите подходящий номер из доступных.\n"
        "4. Подтвердите заявку и отправьте свои контактные данные.\n"
        "5. Дождитесь подтверждения от администратора (в течение 24 часов).\n"
        "6. После подтверждения оплатите бронирование в течение 3 часов.\n"
        "7. Получите уведомление о бронировании с деталями заезда.\n\n"
        "ℹ️ **Важно:**\n"
        "- Бронирование доступно минимум за 1 день до заезда.\n"
        "- Максимальный срок бронирования — 31 день.\n"
        "- Для групп более 14 человек свяжитесь с администратором.\n\n"
        "❌ **Правила отмены бронирования (пошагово)**\n\n"
        "1. Перейдите в раздел 'Мои заявки' и выберите нужную заявку.\n"
        "2. Нажмите 'Отменить заявку' и укажите причину отмены.\n"
        "3. Дождитесь рассмотрения запроса администратором (в течение 24 часов).\n"
        "4. Получите уведомление о подтверждении или отклонения отмены.\n"
        "5. При подтверждении возврат средств будет выполнен в течение 1 рабочего дня.\n\n"
        "ℹ️ **Важно:**\n"
        "- Отмену с возвратом оплаты можно сделать не ранее чем за 7 дней до даты заезда.\n"
        "- При отмене менее чем за 7 дней удерживается 50% стоимости.\n"
        "- Все наши номера находятся на 2м этаже.\n\n"
        "✅ **При нажатии кнопки 'Ознакомлен' вы подтверждаете, что принимаете наши правила и полностью с ними ознакомлены.**"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Ознакомлен", callback_data="rules_accepted"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_start")],
        ]
    )
    await message.answer(
        "⚠️ Пожалуйста, подтвердите согласие с правилами, нажав на кнопку 'Ознакомлен'.",
        reply_markup=kb,
    )
    logging.info(
        f"Пользователь {message.from_user.id} отправил текст во время ознакомления с правилами, напомнили о необходимости подтверждения"
    )


@router.callback_query(F.data == "back_to_start")
async def back_to_start(callback: CallbackQuery, state: FSMContext):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📜 Ознакомиться с правилами", callback_data="show_rules"
                )
            ]
        ]
    )
    await callback.message.edit_text(
        "🌊 **Добро пожаловать в систему бронирования Дача на Байкале! 🌲**\n\n"
        "Мы рады помочь вам спланировать незабываемый отдых на берегу Байкала! "
        "Бронируйте уютные номера, проверяйте свободные даты и получайте поддержку в любое время. "
        "Для начала, пожалуйста, ознакомьтесь с правилами бронирования и отмены.",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    await state.set_state(
        BookingStates.waiting_for_rules_prompt
    )  # Устанавливаем новое состояние
    logging.info(
        f"Пользователь {callback.from_user.id} вернулся к приветственному сообщению, состояние waiting_for_rules_prompt установлено"
    )
    await callback.answer()


@router.callback_query(F.data == "rules_accepted")
async def rules_accepted(callback: CallbackQuery, state: FSMContext):
    from database import session, User
    from config import ADMIN_CHAT_ID, ADMIN_USERNAME

    user_id = callback.from_user.id
    user = session.query(User).filter_by(user_id=user_id).first()
    if user:
        user.rules_accepted = True
        user.username = callback.from_user.username  # Сохраняем или обновляем username
        session.commit()
        logging.info(f"Пользователь {user_id} подтвердил ознакомление с правилами")

    # Отправляем уведомление администратору
    try:
        username = callback.from_user.username or "Аноним"
        await callback.message.bot.send_message(
            ADMIN_CHAT_ID,
            f"👤 Новый пользователь принял соглашение:\n"
            f"ID: {user_id}\n"
            f"Username: @{username}",
            parse_mode="Markdown",
        )
        logging.info(
            f"Уведомление о новом пользователе {user_id} отправлено администратору"
        )
    except Exception as e:
        logging.error(
            f"Ошибка при отправке уведомления администратору о пользователе {user_id}: {str(e)}\n{traceback.format_exc()}"
        )

    # Показываем главное меню
    buttons = [
        [InlineKeyboardButton(text="📅 Забронировать номер", callback_data="book")],
        [InlineKeyboardButton(text="🔎 Проверить свободные даты", callback_data="check_availability")],
        [InlineKeyboardButton(text="📋 Мои заявки", callback_data="my_bookings")],
        [
            InlineKeyboardButton(
                text="⭐ Посмотреть отзывы", callback_data="view_reviews"
            )
        ],
        [InlineKeyboardButton(text="📞 Поддержка", callback_data="support")],
        [InlineKeyboardButton(text="ℹ️ О нас", callback_data="about_us")],
    ]
    if str(user_id) == str(ADMIN_CHAT_ID):
        buttons.append(
            [InlineKeyboardButton(text="👨‍💼 Админ-панель", callback_data="admin")]
        )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.edit_text(
        "🌊 Главное меню системы бронирования Дача на Байкале 🌲", reply_markup=kb
    )
    await state.clear()
    logging.info(
        f"Пользователь {user_id} открыл главное меню после ознакомления, состояние сброшено"
    )
    await callback.answer()


@router.message(F.text, BookingStates.waiting_for_rules_prompt)
async def handle_text_during_rules_prompt(message: Message, state: FSMContext):
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📜 Ознакомиться с правилами", callback_data="show_rules"
                )
            ]
        ]
    )
    await message.answer("⚠️ Нажмите ознакомиться с правилами", reply_markup=kb)
    logging.info(
        f"Пользователь {message.from_user.id} отправил текст до нажатия 'Ознакомиться с правилами', напомнили о необходимости нажать кнопку"
    )


@router.callback_query(F.data == "select_room_type")
async def start_booking(callback: CallbackQuery, state: FSMContext):
    try:
        # Получаем текущие данные из состояния
        data = await state.get_data()
        adults = data.get("adults")
        duration = data.get("duration")
        children = data.get("children", 0)
        children_beds = data.get("children_beds", [])

        # Проверка: если duration отсутствует, перенаправляем на ввод количества суток
        if duration is None:
            await callback.message.edit_text("📅 На сколько суток вы хотите заехать?")
            await state.set_state(BookingStates.waiting_for_duration)
            await callback.answer()
            return

        # Проверка: если adults отсутствует, перенаправляем на ввод количества взрослых
        if adults is None:
            await callback.message.edit_text("👨‍👩‍👧‍👦 Введите количество взрослых:")
            await state.set_state(BookingStates.waiting_for_adults)
            await callback.answer()
            return

        # Очищаем состояние, но сохраняем ключевые данные
        await state.clear()
        await state.update_data(
            duration=duration,
            adults=adults,
            children=children,
            children_beds=children_beds,
        )

        children_needing_beds = sum(children_beds)
        total_people = adults + children_needing_beds
        selected_room_types = await get_optimal_room_combinations(
            total_people=total_people, duration=duration
        )
        if not selected_room_types:
            await callback.message.edit_text(
                "К сожалению, на выбранное количество дней нет свободных номеров.\n"
                "Попробуйте выбрать другие даты.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ Назад", callback_data="back_to_main"
                            )
                        ]
                    ]
                ),
            )
            await callback.answer()
            return
        all_room_types = list(room_type_names.keys())
        await state.update_data(
            selected_room_types=selected_room_types, all_room_types=all_room_types
        )
        kb = []
        for room_type in selected_room_types:
            kb.append(
                [
                    InlineKeyboardButton(
                        text=room_type_names[room_type],
                        callback_data=f"room_type_{room_type}",
                    )
                ]
            )
        kb.append(
            [
                InlineKeyboardButton(
                    text="⬅️ Вернуться к выбору суток", callback_data="back_to_duration"
                )
            ]
        )
        await callback.message.edit_text(
            f"🏠 Выберите тип номера (проживание на {get_duration_text(duration)}):",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=kb),
        )
        logging.info(
            f"Пользователь {callback.from_user.id} вернулся к выбору номера для {total_people} человек на {duration} суток"
        )
        await callback.answer()
    except Exception as e:
        logging.error(f"Ошибка в start_booking: {str(e)}\n{traceback.format_exc()}")
        await callback.message.answer("⚠️ Ошибка при выборе номера. Попробуйте снова.")
        await state.clear()


@router.message(F.text, BookingStates.waiting_for_duration)
async def process_duration(message: Message, state: FSMContext):
    from utils import format_booking_info

    user_id = message.from_user.id
    active_booking = (
        session.query(Booking)
        .filter(
            Booking.user_id == user_id,
            Booking.status.in_(
                ["new", "pending", "awaiting_payment", "awaiting_payment_confirmation"]
            ),
        )
        .order_by(Booking.id.desc())
        .first()
    )
    if active_booking:
        status_mapping = {
            "new": "🟡 Ожидает подтверждения администратора, пожалуйста, ожидайте",
            "pending": "🟡 Ожидает подтверждения",
            "awaiting_payment": "💰 Ожидает оплаты",
            "awaiting_payment_confirmation": "📸 Ожидает подтверждения оплаты",
        }
        status_text = status_mapping.get(active_booking.status, active_booking.status)

        booking_info = (
            f"📌 У вас уже есть активная заявка #{active_booking.id}:\n"
            f"{await format_booking_info(active_booking)}\n"
            f"Статус: {status_text}\n"
        )

        buttons = []
        if active_booking.status in ["new", "pending"]:
            buttons = [
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
            ]
        elif active_booking.status == "awaiting_payment":
            buttons = [
                [
                    InlineKeyboardButton(
                        text="📸 Отправить скриншот",
                        callback_data=f"send_screenshot_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
            ]
        elif active_booking.status == "awaiting_payment_confirmation":
            buttons = [
                [
                    InlineKeyboardButton(
                        text="📸 Отправить скриншот",
                        callback_data=f"send_screenshot_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
            ]

        unique_buttons = []
        seen_callbacks = set()
        for row in buttons:
            key = tuple(button.callback_data for button in row)
            if key not in seen_callbacks:
                seen_callbacks.add(key)
                unique_buttons.append(row)
        buttons = unique_buttons

        buttons.append(
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        )
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

        await message.answer(
            booking_info
            + "\nПожалуйста, завершите действия с текущей заявкой, прежде чем создавать новую.",
            reply_markup=kb,
            parse_mode="HTML",
        )
        logging.info(
            f"Пользователь {user_id} попытался указать длительность, но имеет активную заявка #{active_booking.id} в статусе {active_booking.status}"
        )
        await state.clear()
        return

    try:
        duration = int(message.text)
        if duration <= 0:
            await message.answer(
                "⚠️ Количество суток должно быть больше 0. Введите снова (например, 2):"
            )
            await state.set_state(BookingStates.waiting_for_duration)
            return
        MAX_DURATION = 31
        if duration > MAX_DURATION:
            await message.answer(
                f"⚠️ Максимальная длительность бронирования — {MAX_DURATION} суток. Введите меньшее значение (например, 2):"
            )
            await state.set_state(BookingStates.waiting_for_duration)
            return
        await state.update_data(duration=duration)
        await save_current_booking_draft(message.from_user.id, state)
        await message.answer(
            f"📅 Вы выбрали проживание на {get_duration_text(duration)}.\n"
            "👨‍👩‍👧‍👦 Сколько взрослых будет проживать? (например, 2):",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        await state.set_state(BookingStates.waiting_for_adults)
        logging.info(
            f"Пользователь {message.from_user.id} указал длительность: {duration} суток"
        )
    except ValueError:
        await message.answer("⚠️ Пожалуйста, введите число (например, 2):")
        await state.set_state(BookingStates.waiting_for_duration)


@router.message(BookingStates.waiting_for_duration)
async def handle_invalid_duration_input(message: Message, state: FSMContext):
    content_type = message.content_type
    await message.answer(
        "⚠️ Пожалуйста, введите количество суток числом (например, 2):",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await state.set_state(BookingStates.waiting_for_duration)
    logging.debug(
        f"Пользователь {message.from_user.id} отправил некорректный тип контента ({content_type}) в состоянии waiting_for_duration"
    )


@router.message(BookingStates.waiting_for_adults, ~F.text)
async def handle_invalid_adults_input(message: Message, state: FSMContext):
    content_type = message.content_type
    await message.answer(
        "⚠️ Пожалуйста, введите количество взрослых числом (например, 2):",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await state.set_state(BookingStates.waiting_for_adults)
    logging.debug(
        f"Пользователь {message.from_user.id} отправил некорректный тип контента ({content_type}) в состоянии waiting_for_adults"
    )


@router.message(BookingStates.waiting_for_children, ~F.text)
async def handle_invalid_children_input(message: Message, state: FSMContext):
    content_type = message.content_type
    await message.answer(
        "⚠️ Пожалуйста, введите количество детей числом (например, 0 или 2):",
        reply_markup=types.ReplyKeyboardRemove(),
    )
    await state.set_state(BookingStates.waiting_for_children)
    logging.debug(
        f"Пользователь {message.from_user.id} отправил некорректный тип контента ({content_type}) в состоянии waiting_for_children"
    )


@router.callback_query(
    F.data == "back_to_children",
    StateFilter(
        BookingStates.waiting_for_bed_choice, BookingStates.waiting_for_room_type
    ),
)
async def back_to_children(callback: CallbackQuery, state: FSMContext):
    try:
        logging.debug(
            f"Обработка callback: data={callback.data}, state={await state.get_state()}"
        )
        await state.update_data(children_beds=[], current_child=None)
        await callback.message.edit_text("👶 Введите количество детей 0-3 лет:")
        await state.set_state(BookingStates.waiting_for_children)
        logging.info(
            f"Пользователь {callback.from_user.id} вернулся к выбору количества детей"
        )
        await callback.answer()
    except Exception as e:
        logging.error(
            f"Ошибка в back_to_children для пользователя {callback.from_user.id}: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.message.answer("⚠️ Произошла ошибка. Попробуйте снова.")
        await state.clear()
        await callback.message.answer("📅 Введите количество суток:")
        await state.set_state(BookingStates.waiting_for_duration)
        await callback.answer()


@router.callback_query(
    F.data == "back_to_adults",
    StateFilter(
        BookingStates.waiting_for_room_type,
        BookingStates.waiting_for_adults,
        BookingStates.waiting_for_children,
    ),
)
async def back_to_adults(callback: CallbackQuery, state: FSMContext):
    try:
        logging.debug(
            f"Обработка callback: data={callback.data}, state={await state.get_state()}"
        )
        # Проверка: пользователь должен быть тем же, кто начал бронирование
        data = await state.get_data()
        user_id = data.get("user_id", callback.from_user.id)
        if callback.from_user.id != user_id:
            await callback.answer(
                "У вас нет доступа к этому действию.", show_alert=True
            )
            return
        await state.update_data(children=0, children_beds=[])
        await callback.message.edit_text("👨‍👩‍👧‍👦 Введите количество взрослых:")
        await state.set_state(BookingStates.waiting_for_adults)
        logging.info(
            f"Пользователь {callback.from_user.id} вернулся к выбору количества взрослых"
        )
        await callback.answer()
    except Exception as e:
        logging.error(
            f"Ошибка в back_to_adults для пользователя {callback.from_user.id}: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.message.answer("⚠️ Произошла ошибка. Попробуйте снова.")
        await state.clear()
        await callback.message.answer("📅 Введите количество суток (например, 2):")
        await state.set_state(BookingStates.waiting_for_duration)
        await callback.answer()


@router.message(BookingStates.waiting_for_adults, F.text)
async def process_adults(message: Message, state: FSMContext):
    from utils import format_booking_info

    user_id = message.from_user.id
    active_booking = (
        session.query(Booking)
        .filter(
            Booking.user_id == user_id,
            Booking.status.in_(
                ["new", "pending", "awaiting_payment", "awaiting_payment_confirmation"]
            ),
        )
        .order_by(Booking.id.desc())
        .first()
    )
    if active_booking:
        status_mapping = {
            "new": "🟡 Ожидает подтверждения администратора, пожалуйста, ожидайте",
            "pending": "🟡 Ожидает подтверждения",
            "awaiting_payment": "💰 Ожидает оплаты",
            "awaiting_payment_confirmation": "📸 Ожидает подтверждения оплаты",
        }
        status_text = status_mapping.get(active_booking.status, active_booking.status)

        booking_info = (
            f"📌 У вас уже есть активная заявка #{active_booking.id}:\n"
            f"{await format_booking_info(active_booking)}\n"
            f"Статус: {status_text}\n"
        )

        buttons = []
        if active_booking.status in ["new", "pending"]:
            buttons = [
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
            ]
        elif active_booking.status == "awaiting_payment":
            buttons = [
                [
                    InlineKeyboardButton(
                        text="📸 Отправить скриншот",
                        callback_data=f"send_screenshot_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
            ]
        elif active_booking.status == "awaiting_payment_confirmation":
            buttons = [
                [
                    InlineKeyboardButton(
                        text="📸 Отправить скриншот",
                        callback_data=f"send_screenshot_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
            ]

        unique_buttons = []
        seen_callbacks = set()
        for row in buttons:
            key = tuple(button.callback_data for button in row)
            if key not in seen_callbacks:
                seen_callbacks.add(key)
                unique_buttons.append(row)
        buttons = unique_buttons

        buttons.append(
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        )
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

        await message.answer(
            booking_info
            + "\nПожалуйста, завершите действия с текущей заявкой, прежде чем создавать новую.",
            reply_markup=kb,
            parse_mode="HTML",
        )
        logging.info(
            f"Пользователь {user_id} попытался указать количество взрослых, но имеет активную заявку #{active_booking.id} в статусе {active_booking.status}"
        )
        await state.clear()
        return

    try:
        adults = int(message.text.strip())
        logging.debug(f"Введено число взрослых: {adults}")
        if adults <= 0:
            await message.answer(
                "⚠️ Количество взрослых должно быть больше 0. Введите снова (например, 2):"
            )
            await state.set_state(BookingStates.waiting_for_adults)
            logging.debug(f"Отклонён ввод: {adults} (меньше или равно 0)")
            return

        MAX_ADULTS = 14
        if adults > MAX_ADULTS:
            await message.answer(
                f"⚠️ Максимальное количество взрослых — {MAX_ADULTS}. Введите меньшее значение (например, 2):"
            )
            await state.set_state(BookingStates.waiting_for_adults)
            logging.debug(f"Отклонён ввод: {adults} (больше {MAX_ADULTS})")
            return

        await state.update_data(adults=adults)
        await save_current_booking_draft(message.from_user.id, state)
        await message.answer("👶 Сколько детей будет проживать? (до 3х лет):")
        await state.set_state(BookingStates.waiting_for_children)
        logging.info(f"Пользователь {message.from_user.id} указал {adults} взрослых")
    except ValueError as e:
        await message.answer("⚠️ Пожалуйста, введите число (например, 2):")
        await state.set_state(BookingStates.waiting_for_adults)
        logging.debug(
            f"Ошибка ValueError: некорректное значение '{message.text}', ошибка: {str(e)}"
        )
    except Exception as e:
        await message.answer(
            "⚠️ Произошла ошибка при обработке ввода. Пожалуйста, введите число (например, 2):"
        )
        await state.set_state(BookingStates.waiting_for_adults)
        logging.error(
            f"Неожиданная ошибка в process_adults для пользователя {user_id}: {str(e)}\n{traceback.format_exc()}"
        )


@router.message(BookingStates.waiting_for_children, F.text)
async def process_children(message: Message, state: FSMContext):
    from utils import format_booking_info

    user_id = message.from_user.id
    active_booking = (
        session.query(Booking)
        .filter(
            Booking.user_id == user_id,
            Booking.status.in_(
                ["new", "pending", "awaiting_payment", "awaiting_payment_confirmation"]
            ),
        )
        .order_by(Booking.id.desc())
        .first()
    )
    if active_booking:
        status_mapping = {
            "new": "🟡 Ожидает подтверждения администратора, пожалуйста, ожидайте",
            "pending": "🟡 Ожидает подтверждения",
            "awaiting_payment": "💰 Ожидает оплаты",
            "awaiting_payment_confirmation": "📸 Ожидает подтверждения оплаты",
        }
        status_text = status_mapping.get(active_booking.status, active_booking.status)

        booking_info = (
            f"📌 У вас уже есть активная заявка #{active_booking.id}:\n"
            f"{await format_booking_info(active_booking)}\n"
            f"Статус: {status_text}\n"
        )

        buttons = []
        if active_booking.status in ["new", "pending"]:
            buttons = [
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
            ]
        elif active_booking.status == "awaiting_payment":
            buttons = [
                [
                    InlineKeyboardButton(
                        text="📸 Отправить скриншот",
                        callback_data=f"send_screenshot_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
            ]
        elif active_booking.status == "awaiting_payment_confirmation":
            buttons = [
                [
                    InlineKeyboardButton(
                        text="📸 Отправить скриншот",
                        callback_data=f"send_screenshot_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
            ]

        unique_buttons = []
        seen_callbacks = set()
        for row in buttons:
            key = tuple(button.callback_data for button in row)
            if key not in seen_callbacks:
                seen_callbacks.add(key)
                unique_buttons.append(row)
        buttons = unique_buttons

        buttons.append(
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        )
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

        await message.answer(
            booking_info
            + "\nПожалуйста, завершите действия с текущей заявкой, прежде чем создавать новую.",
            reply_markup=kb,
            parse_mode="HTML",
        )
        logging.info(
            f"Пользователь {user_id} попытался указать количество детей, но имеет активную заявку #{active_booking.id} в статусе {active_booking.status}"
        )
        await state.clear()
        return

    try:
        children = int(message.text)
        logging.debug(f"Введено число детей: {children}")
        if children < 0:
            await message.answer(
                "⚠️ Количество детей не может быть отрицательным. Введите снова:"
            )
            await state.set_state(BookingStates.waiting_for_children)
            logging.debug(f"Отклонён ввод: {children} (отрицательное)")
            return
        MAX_CHILDREN = 5
        if children > MAX_CHILDREN:
            await message.answer(
                f"⚠️ Максимальное количество детей — {MAX_CHILDREN}. Введите меньшее значение:"
            )
            await state.set_state(BookingStates.waiting_for_children)
            logging.debug(f"Отклонён ввод: {children} (больше {MAX_CHILDREN})")
            return
        await state.update_data(children=children)
        await save_current_booking_draft(message.from_user.id, state)
        if children == 0:
            await state.update_data(children_beds=[])
            await save_current_booking_draft(message.from_user.id, state)
            await select_room_type(message, state)
        else:
            await state.update_data(children_beds=[0] * children, current_child=1)
            await save_current_booking_draft(message.from_user.id, state)
            await message.answer(
                f"Ребёнок 1 из {children}:\n"
                f"Нужно ли отдельное спальное место?\n"
                f"Если кровать нужна, сумма бронирования будет пересчитана: ребёнок считается как взрослый гость.\n"
                f"Если кровать не нужна, ребёнок спит с родителями и не добавляется к платным местам.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="Да, нужна кровать", callback_data="bed_yes"
                            )
                        ],
                        [InlineKeyboardButton(text="Нет", callback_data="bed_no")],
                    ]
                ),
            )
            logging.debug(
                f"Сохранено состояние: children={children}, children_beds={[0] * children}, current_child=1"
            )
            await state.set_state(BookingStates.waiting_for_bed_choice)
        logging.info(f"Пользователь {message.from_user.id} указал {children} детей")
    except ValueError as e:
        await message.answer("⚠️ Пожалуйста, введите число (например, 0 или 2):")
        await state.set_state(BookingStates.waiting_for_children)
        logging.debug(
            f"Ошибка ValueError: некорректное значение '{message.text}', ошибка: {str(e)}"
        )
    except Exception as e:
        await message.answer(
            "⚠️ Произошла ошибка при обработке ввода. Пожалуйста, введите число (например, 0 или 2):"
        )
        await state.set_state(BookingStates.waiting_for_children)
        logging.error(
            f"Неожиданная ошибка в process_children для пользователя {user_id}: {str(e)}\n{traceback.format_exc()}"
        )


@router.callback_query(StateFilter(BookingStates.waiting_for_child_sleeping))
async def process_child_sleeping(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    children = data.get("children", 0)
    child_index = data.get("child_index", 0)
    children_beds = data.get("children_beds", [])

    # Добавляем выбор текущего ребёнка
    needs_bed = 1 if callback.data == "child_needs_bed" else 0
    children_beds.append(needs_bed)
    await state.update_data(children_beds=children_beds)
    await save_current_booking_draft(callback.from_user.id, state)

    # Переходим к следующему ребёнку или к выбору номера
    child_index += 1
    if child_index < children:
        await callback.message.edit_text(
            f"🛏️ Будет ли ребёнку №{child_index + 1} нужно отдельное спальное место? Если да, ребёнок будет учтён как платное место.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="С родителями", callback_data="child_with_parents"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="Спальное место", callback_data="child_needs_bed"
                        )
                    ],
                ]
            ),
        )
        await state.update_data(child_index=child_index)
    else:
        await callback.message.delete()
        await process_room_selection(callback.message, state)
    logging.info(
        f"Пользователь {callback.from_user.id} указал для ребёнка №{child_index + 1}: {'спальное место' if needs_bed else 'с родителями'}"
    )


async def process_room_selection(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        adults = data.get("adults", 1)
        duration = data.get("duration", 1)
        children = data.get("children", 0)
        children_beds = data.get("children_beds", [])
        children_needing_beds = sum(children_beds)
        total_people = adults + children_needing_beds
        logging.debug(
            f"Расчёт total_people: adults={adults}, children_needing_beds={children_needing_beds}, children={children}, total={total_people}"
        )

        # Сохраняем user_id в состоянии
        await state.update_data(user_id=message.from_user.id)

        # Получаем подходящие номера с учётом duration
        room_types = await get_optimal_room_combinations(
            total_people, duration=duration
        )
        logging.debug(
            f"Доступные номера для {total_people} человек на {duration} суток: {room_types}"
        )

        if not room_types:
            await message.answer(
                f"⚠️ Нет доступных номеров для {total_people} человек на {get_duration_text(duration)}. Попробуйте изменить количество гостей или длительность.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ Назад к выбору взрослых",
                                callback_data="back_to_adults",
                            )
                        ]
                    ]
                ),
            )
            await state.set_state(BookingStates.waiting_for_adults)
            return

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=room_type_names[rt], callback_data=f"room_{rt}"
                    )
                ]
                for rt in room_types
            ]
            + [
                [
                    InlineKeyboardButton(
                        text="⬅️ Вернуться к выбору суток",
                        callback_data="back_to_duration",
                    )
                ]
            ]
        )
        await message.answer(
            f"🏠 Выберите номер для {total_people} человек на {get_duration_text(duration)}:",
            reply_markup=kb,
        )
        await state.set_state(BookingStates.waiting_for_room_type)
        logging.info(
            f"Пользователь {message.from_user.id} выбирает номер для {total_people} человек на {duration} суток"
        )
    except Exception as e:
        logging.error(
            f"Ошибка в process_room_selection для пользователя {message.from_user.id}: {str(e)}\n{traceback.format_exc()}"
        )
        await message.answer("⚠️ Произошла ошибка при выборе номера. Попробуйте снова.")
        await state.clear()
        await message.answer("📅 Введите количество суток (например, 2):")
        await state.set_state(BookingStates.waiting_for_duration)


@router.callback_query(F.data.startswith("room_type_"))
async def show_calendar(callback: CallbackQuery, state: FSMContext):
    try:
        room_type = callback.data.split("_")[2]
        # Получаем текущий год из базы
        with session as db_session:
            booking_year = db_session.query(BookingYear).first()
            current_year = booking_year.year if booking_year else 2025
        month = 6  # Начальный месяц (июнь)
        duration = 1  # По умолчанию 1 сутки

        data = await state.get_data()
        duration = data.get("duration", duration)
        selected_room_types = data.get("selected_room_types", [])

        await state.update_data(room_type=room_type, is_admin_mode=False)
        await save_current_booking_draft(callback.from_user.id, state)

        booked_dates = await get_booked_dates(room_type)

        calendar = await generate_calendar(
            year=current_year,
            month=month,
            booked_dates=booked_dates,
            room_type=room_type,
            duration=duration,
            state=state,
        )

        await callback.message.edit_text(
            f"📅 Свободные даты заезда для варианта «{room_type_names.get(room_type, room_type)}» в {current_year} году:\n"
            f"🟢 — свободные даты для заезда\n"
            f"🔴 — занятые даты\n"
            f"🔒 — недоступные даты",
            reply_markup=calendar,
        )
        await state.set_state(BookingStates.waiting_for_start_date)
        await callback.answer()
    except Exception as e:
        logging.error(f"Ошибка в show_calendar: {str(e)}\n{traceback.format_exc()}")
        await callback.message.answer(
            "⚠️ Ошибка при загрузке календаря. Попробуйте снова или свяжитесь с поддержкой."
        )
        await state.clear()


@router.callback_query(F.data == "locked")
async def handle_locked_date(callback: CallbackQuery, state: FSMContext):
    booking_year = session.query(BookingYear).first()
    current_year = booking_year.year if booking_year else 2025
    await callback.answer(
        f"Эта дата недоступна для бронирования. Выберите дату в {current_year} году.",
        show_alert=True,
    )
    logging.info(
        f"Пользователь {callback.from_user.id} попытался выбрать недоступную дату (locked)"
    )


@router.callback_query(F.data == "ignore")
async def handle_ignore(callback: CallbackQuery, state: FSMContext):
    await callback.answer()  # Просто игнорируем без уведомления
    logging.debug(f"Пользователь {callback.from_user.id} нажал кнопку 'ignore'")


@router.callback_query(F.data == "back_to_duration")
async def back_to_duration(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("📅 На сколько суток вы хотите заехать?")
    await state.set_state(BookingStates.waiting_for_duration)
    logging.info(f"Пользователь {callback.from_user.id} вернулся к вводу длительности")




def get_booking_year_value() -> int:
    booking_year = session.query(BookingYear).first()
    return booking_year.year if booking_year else date.today().year


def get_availability_calendar_text(room_type: str, year: int, people: int, duration: int) -> str:
    return (
        f"📅 Свободные даты для {room_type_names.get(room_type, room_type)}\n"
        f"👥 Гостей: {people}\n"
        f"🌙 Проживание: {get_duration_text(duration)}\n"
        f"📆 Год: {year}\n\n"
        "\U0001f7e2 \u0441\u0432\u043e\u0431\u043e\u0434\u043d\u043e \u0434\u043b\u044f \u0437\u0430\u0435\u0437\u0434\u0430\n"
        "\U0001f534 \u0437\u0430\u043d\u044f\u0442\u043e\n"
        "\u26aa \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u043e\n\n"
        "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u0441\u0432\u043e\u0431\u043e\u0434\u043d\u0443\u044e \u0434\u0430\u0442\u0443, \u0447\u0442\u043e\u0431\u044b \u043f\u043e\u0441\u043c\u043e\u0442\u0440\u0435\u0442\u044c \u043f\u043e\u0434\u0440\u043e\u0431\u043d\u043e\u0441\u0442\u0438."
    )


def get_availability_room_keyboard(room_types: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=room_type_names.get(room_type, room_type),
                callback_data=f"availability_room_{room_type}",
            )
        ]
        for room_type in room_types
    ]
    rows.append([InlineKeyboardButton(text="🌙 Изменить срок", callback_data="availability_back_duration")])
    rows.append([InlineKeyboardButton(text="\U0001f465 \u0418\u0437\u043c\u0435\u043d\u0438\u0442\u044c \u0433\u043e\u0441\u0442\u0435\u0439", callback_data="check_availability")])
    rows.append([InlineKeyboardButton(text="\U0001f3e0 \u0413\u043b\u0430\u0432\u043d\u043e\u0435 \u043c\u0435\u043d\u044e", callback_data="back_to_main")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def add_availability_calendar_back_buttons(calendar: InlineKeyboardMarkup) -> InlineKeyboardMarkup:
    calendar.inline_keyboard.append(
        [InlineKeyboardButton(text="\u2b05\ufe0f \u041a \u0432\u044b\u0431\u043e\u0440\u0443 \u043d\u043e\u043c\u0435\u0440\u0430", callback_data="availability_back_rooms")]
    )
    calendar.inline_keyboard.append(
        [InlineKeyboardButton(text="\U0001f3e0 \u0413\u043b\u0430\u0432\u043d\u043e\u0435 \u043c\u0435\u043d\u044e", callback_data="back_to_main")]
    )
    return calendar


async def show_availability_room_choice(target, state: FSMContext, people: int, duration: int, room_types: list[str]):
    await state.update_data(
        availability_people=people,
        availability_duration=duration,
        availability_room_types=room_types,
        duration=duration,
    )
    text = (
        "\U0001f4c5 \u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0441\u0432\u043e\u0431\u043e\u0434\u043d\u044b\u0445 \u0434\u0430\u0442\n\n"
        f"\u0413\u043e\u0441\u0442\u0435\u0439: {people}\n"
        f"Срок: {get_duration_text(duration)}\n"
        "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043d\u043e\u043c\u0435\u0440 \u043a\u043d\u043e\u043f\u043a\u043e\u0439 \u043d\u0438\u0436\u0435."
    )
    markup = availability_rooms_keyboard(room_types, room_type_names)
    if isinstance(target, CallbackQuery):
        try:
            await target.message.delete()
        except TelegramBadRequest:
            pass
        sent = await target.message.answer(text, reply_markup=markup)
    else:
        sent = await target.answer(text, reply_markup=markup)
    await state.update_data(availability_message_id=sent.message_id)
    await state.set_state(BookingStates.waiting_for_availability_room)


@router.callback_query(F.data == "check_availability")
async def start_availability_check(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    sent = await callback.message.answer(
        "\U0001f4c5 \u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0441\u0432\u043e\u0431\u043e\u0434\u043d\u044b\u0445 \u0434\u0430\u0442\n\n\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u0433\u043e\u0441\u0442\u0435\u0439:",
        reply_markup=availability_people_keyboard(),
    )
    await state.update_data(previous_menu="main", availability_message_id=sent.message_id)
    await state.set_state(BookingStates.waiting_for_availability_people)
    await callback.answer()
    logging.info("User %s started availability check", callback.from_user.id)


async def safe_update_availability_prompt(message: Message, state: FSMContext, text: str):
    data = await state.get_data()
    message_id = data.get("availability_message_id")
    if message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=message_id,
                text=text,
            )
            return
        except TelegramBadRequest as e:
            logging.warning("Availability prompt edit failed: %s", e)
    sent = await message.answer(text, reply_markup=availability_people_keyboard())
    await state.update_data(availability_message_id=sent.message_id)


async def ask_availability_duration(message: Message, state: FSMContext, people: int):
    data = await state.get_data()
    message_id = data.get("availability_message_id")
    text = (
        "📅 Проверка свободных дат\n\n"
        f"Гостей: {people}\n"
        "Введите, на сколько суток планируете проживание. Например: 2"
    )
    if message_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=message_id,
                text=text,
            )
            await state.set_state(BookingStates.waiting_for_availability_duration)
            return
        except TelegramBadRequest as e:
            logging.warning("Availability duration prompt edit failed: %s", e)
    sent = await message.answer(text, reply_markup=availability_people_keyboard())
    await state.update_data(availability_message_id=sent.message_id)
    await state.set_state(BookingStates.waiting_for_availability_duration)


async def exit_availability_to_main(message: Message, state: FSMContext):
    data = await state.get_data()
    message_id = data.get("availability_message_id")
    try:
        await message.delete()
    except TelegramBadRequest:
        pass


async def safe_edit_or_answer(message, text: str, **kwargs):
    try:
        return await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if any(
            marker in str(e)
            for marker in (
                "there is no text in the message to edit",
                "message to edit not found",
                "message can't be edited",
            )
        ):
            try:
                await message.delete()
            except TelegramBadRequest:
                pass
            return await message.answer(text, **kwargs)
        raise
    if message_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=message_id)
        except TelegramBadRequest:
            pass
    await show_main_menu_message(message, state)


@router.message(BookingStates.waiting_for_availability_people, ~F.text)
async def process_availability_people_not_text(message: Message, state: FSMContext):
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    await safe_update_availability_prompt(
        message,
        state,
            "\U0001f4c5 \u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0441\u0432\u043e\u0431\u043e\u0434\u043d\u044b\u0445 \u0434\u0430\u0442\n\n\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u0433\u043e\u0441\u0442\u0435\u0439 \u0446\u0438\u0444\u0440\u043e\u0439, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440 3."
    )


@router.message(BookingStates.waiting_for_availability_people, F.text)
async def process_availability_people(message: Message, state: FSMContext):
    if message.text.strip() == BTN_MAIN_MENU or "Главное меню" in message.text:
        await exit_availability_to_main(message, state)
        return

    try:
        people = int(message.text.strip())
    except ValueError:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        await safe_update_availability_prompt(
            message,
            state,
            "\U0001f4c5 \u041f\u0440\u043e\u0432\u0435\u0440\u043a\u0430 \u0441\u0432\u043e\u0431\u043e\u0434\u043d\u044b\u0445 \u0434\u0430\u0442\n\n\u0412\u0432\u0435\u0434\u0438\u0442\u0435 \u043a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u043e \u0433\u043e\u0441\u0442\u0435\u0439 \u0446\u0438\u0444\u0440\u043e\u0439, \u043d\u0430\u043f\u0440\u0438\u043c\u0435\u0440 3."
        )
        return

    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    if people < 1 or people > 14:
        await safe_update_availability_prompt(
            message,
            state,
            "📅 Проверка свободных дат\n\nМожно указать от 1 до 14 гостей. Введите число еще раз."
        )
        return

    await state.update_data(availability_people=people)
    await ask_availability_duration(message, state, people)
    logging.info("User %s entered availability people=%s", message.from_user.id, people)
    return


@router.message(BookingStates.waiting_for_availability_duration, ~F.text)
async def process_availability_duration_not_text(message: Message, state: FSMContext):
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    data = await state.get_data()
    await ask_availability_duration(message, state, data.get("availability_people", 1))


@router.message(BookingStates.waiting_for_availability_duration, F.text)
async def process_availability_duration(message: Message, state: FSMContext):
    text = message.text.strip()
    if text == BTN_MAIN_MENU or "Главное меню" in text:
        await exit_availability_to_main(message, state)
        return
    data = await state.get_data()
    people = data.get("availability_people", 1)
    try:
        duration = int(text)
    except ValueError:
        try:
            await message.delete()
        except TelegramBadRequest:
            pass
        await ask_availability_duration(message, state, people)
        return

    try:
        await message.delete()
    except TelegramBadRequest:
        pass

    if duration < 1 or duration > 31:
        await safe_update_availability_prompt(
            message,
            state,
            "📅 Проверка свободных дат\n\nМожно указать срок от 1 до 31 суток. Введите число еще раз.",
        )
        await state.set_state(BookingStates.waiting_for_availability_duration)
        return

    room_types = await get_optimal_room_combinations(people, duration=duration)
    if not room_types:
        await safe_update_availability_prompt(
            message,
            state,
            "\U0001f614 \u0414\u043b\u044f \u0442\u0430\u043a\u043e\u0433\u043e \u043a\u043e\u043b\u0438\u0447\u0435\u0441\u0442\u0432\u0430 \u0433\u043e\u0441\u0442\u0435\u0439 \u043d\u0435\u0442 \u043f\u043e\u0434\u0445\u043e\u0434\u044f\u0449\u0438\u0445 \u043d\u043e\u043c\u0435\u0440\u043e\u0432."
        )
        return

    data = await state.get_data()
    message_id = data.get("availability_message_id")
    if message_id:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=message_id)
        except TelegramBadRequest:
            pass
    await show_availability_room_choice(message, state, people, duration, room_types)
    logging.info("User %s requested availability for %s people, duration=%s: %s", message.from_user.id, people, duration, room_types)


@router.callback_query(F.data == "availability_back_rooms")
async def availability_back_rooms(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    people = data.get("availability_people")
    duration = data.get("availability_duration", data.get("duration", 1))
    room_types = data.get("availability_room_types") or []
    if not people or not room_types:
        await start_availability_check(callback, state)
        return
    await show_availability_room_choice(callback, state, people, duration, room_types)
    await callback.answer()


@router.callback_query(F.data == "availability_back_duration")
async def availability_back_duration(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    people = data.get("availability_people")
    if not people:
        await start_availability_check(callback, state)
        return
    try:
        await callback.message.delete()
    except TelegramBadRequest:
        pass
    sent = await callback.message.answer(
        "📅 Проверка свободных дат\n\n"
        f"Гостей: {people}\n"
        "Введите, на сколько суток планируете проживание. Например: 2",
        reply_markup=availability_people_keyboard(),
    )
    await state.update_data(availability_message_id=sent.message_id)
    await state.set_state(BookingStates.waiting_for_availability_duration)
    await callback.answer()


@router.message(BookingStates.waiting_for_availability_room, F.text)
async def process_availability_room_reply(message: Message, state: FSMContext):
    data = await state.get_data()
    text = message.text.strip()
    if text == BTN_MAIN_MENU or "Главное меню" in text:
        await exit_availability_to_main(message, state)
        return
    if text == BTN_CHANGE_GUESTS:
        fake_callback = MessageBackedCallback(message, "check_availability")
        await start_availability_check(fake_callback, state)
        return

    room_types = data.get("availability_room_types") or []
    room_type = next((candidate for candidate in room_types if text == room_type_names.get(candidate, candidate)), None)
    if not room_type:
        await message.answer(
            "\u0412\u044b\u0431\u0435\u0440\u0438\u0442\u0435 \u043d\u043e\u043c\u0435\u0440 \u043a\u043d\u043e\u043f\u043a\u043e\u0439 \u043d\u0438\u0436\u0435.",
            reply_markup=availability_rooms_keyboard(room_types, room_type_names),
        )
        return

    try:
        await message.delete()
    except TelegramBadRequest:
        pass
    prompt_id = data.get("availability_message_id")
    if prompt_id:
        try:
            await message.bot.delete_message(message.chat.id, prompt_id)
        except TelegramBadRequest:
            pass

    people = data.get("availability_people", 1)
    duration = data.get("availability_duration", data.get("duration", 1))
    year = get_booking_year_value()
    month = 6
    booked_dates = await get_booked_dates(room_type)
    await state.update_data(room_type=room_type, duration=duration, availability_people=people)
    calendar = await generate_calendar(
        year=year,
        month=month,
        booked_dates=booked_dates,
        min_date=date.today(),
        room_type=room_type,
        duration=duration,
        state=state,
    )
    add_availability_calendar_back_buttons(calendar)
    sent = await message.answer(get_availability_calendar_text(room_type, year, people, duration), reply_markup=calendar)
    await state.update_data(availability_message_id=sent.message_id)
    await send_reply_keyboard(message, availability_calendar_keyboard())
    await state.set_state(BookingStates.viewing_availability_calendar)
    logging.info("User %s opened availability calendar for %s, people=%s", message.from_user.id, room_type, people)


@router.message(BookingStates.viewing_availability_calendar, F.text.in_({BTN_MAIN_MENU, BTN_CHANGE_GUESTS, BTN_BACK_ROOMS}))
async def process_availability_calendar_reply(message: Message, state: FSMContext):
    if message.text == BTN_MAIN_MENU or "Главное меню" in message.text:
        await exit_availability_to_main(message, state)
        return
    if message.text == BTN_CHANGE_GUESTS:
        fake_callback = MessageBackedCallback(message, "check_availability")
        await start_availability_check(fake_callback, state)
        return
    data = await state.get_data()
    people = data.get("availability_people")
    duration = data.get("availability_duration", data.get("duration", 1))
    room_types = data.get("availability_room_types") or []
    if not people or not room_types:
        fake_callback = MessageBackedCallback(message, "check_availability")
        await start_availability_check(fake_callback, state)
        return
    await show_availability_room_choice(message, state, people, duration, room_types)


@router.callback_query(F.data.startswith("availability_room_"))
async def show_availability_calendar(callback: CallbackQuery, state: FSMContext):
    room_type = callback.data.split("_", 2)[2]
    data = await state.get_data()
    people = data.get("availability_people", 1)
    duration = data.get("availability_duration", data.get("duration", 1))
    year = get_booking_year_value()
    month = 6
    booked_dates = await get_booked_dates(room_type)
    await state.update_data(room_type=room_type, duration=duration, availability_people=people)
    calendar = await generate_calendar(
        year=year,
        month=month,
        booked_dates=booked_dates,
        min_date=date.today(),
        room_type=room_type,
        duration=duration,
        state=state,
    )
    add_availability_calendar_back_buttons(calendar)
    await callback.message.edit_text(get_availability_calendar_text(room_type, year, people, duration), reply_markup=calendar)
    await send_reply_keyboard(callback.message, availability_calendar_keyboard())
    await state.set_state(BookingStates.viewing_availability_calendar)
    await callback.answer()
    logging.info("User %s opened availability calendar for %s, people=%s", callback.from_user.id, room_type, people)


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "📖 Как забронировать номер:\n"
        "1. Нажмите 'Забронировать номер'.\n"
        "2. Укажите количество взрослых и детей.\n"
        "3. Выберите номер и даты.\n"
        "4. Подтвердите бронирование и отправьте контакт.\n"
        "5. Дождитесь подтверждения от администратора.\n\n"
        "📞 Для помощи: /support"
    )


@router.callback_query(F.data == "faq")
async def show_faq(callback: CallbackQuery, state: FSMContext):
    # Сохраняем предыдущее состояние для кнопки "Назад"
    await state.update_data(previous_menu="about_us")

    faq_text = (
        "❓ Частые вопросы от гостей\n\n"
        "▪️ Куда выбрасывать мусор?\n"
        "На территории @dachanabaikale, и за территорией есть большой бак, которым вы можете воспользоваться при необходимости.\n\n"
        "▪️ Если у меня есть дети, куда выбрасывать фантики от конфет?\n"
        "Пожалуйста, заранее покажите детям, куда можно выбросить фантики, чтобы они не оставались на территории. Мусорный бак находится на территории и за её пределами.\n\n"
        "▪️ Можно ли включать громкую музыку?\n"
        "Громкая музыка разрешена до 23:00. В селе Максимиха дежурят участковые полицейские, которые следят за соблюдением тишины.\n\n"
        "▪️ Что делать, если холодно?\n"
        "Если вам кажется, что прохладно, мы принесём вам обогреватель.\n"
    )
    try:
        await callback.message.edit_text(faq_text, reply_markup=None)
    except TelegramBadRequest as e:
        if "there is no text in the message to edit" in str(e):
            await callback.message.delete()
            await callback.message.answer(faq_text, reply_markup=about_menu_keyboard())
        else:
            raise
    logging.info(
        f"Пользователь {callback.from_user.id} просмотрел частые вопросы от гостей"
    )
    await callback.answer()


@router.message(BookingStates.waiting_for_room_type)
async def select_room_type(message: Message, state: FSMContext):
    data = await state.get_data()
    duration = data.get("duration", 1)
    adults = data.get("adults", 1)
    children = data.get("children", 0)
    children_beds = data.get("children_beds", [0] * children)
    children_needing_beds = sum(children_beds)

    total_people = adults + children_needing_beds

    logging.debug(
        f"select_room_type: adults={adults}, children={children}, children_beds={children_beds}, children_needing_beds={children_needing_beds}, total_people={total_people}, duration={duration}"
    )

    # Проверка общего количества человек
    MAX_TOTAL_PEOPLE = 14
    if total_people > MAX_TOTAL_PEOPLE:
        await message.answer(
            f"⚠️ Общее количество человек (взрослых: {adults}, детей с кроватями: {children_needing_beds}) превышает максимальную вместимость ({MAX_TOTAL_PEOPLE}). "
            "Попробуйте уменьшить количество взрослых или детей с кроватями.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад к выбору взрослых",
                            callback_data="back_to_adults",
                        )
                    ]
                ]
            ),
        )
        await state.set_state(BookingStates.waiting_for_adults)
        return

    optimal_rooms = await get_optimal_room_combinations(
        total_people=total_people, duration=duration
    )
    if not optimal_rooms:
        await message.answer(
            f"⚠️ К сожалению, нет подходящих номеров для {total_people} человек (взрослых: {adults}, детей с кроватями: {children_needing_beds}) на {get_duration_text(duration)}. "
            "Попробуйте уменьшить количество гостей или выбрать другую длительность.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад к выбору взрослых",
                            callback_data="back_to_adults",
                        )
                    ]
                ]
            ),
        )
        await state.set_state(BookingStates.waiting_for_adults)
        return

    await state.update_data(selected_room_types=optimal_rooms)
    buttons = [
        [InlineKeyboardButton(text=room_type_names[room], callback_data=f"room_{room}")]
        for room in optimal_rooms
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.answer(
        f"👨‍👩‍👧‍👦 Вы бронируете для {total_people} человек ({adults} взрослых, {children} детей, из них {children_needing_beds} с кроватями) на {get_duration_text(duration)}.\n"
        f"💰 Дети с отдельной кроватью учитываются как платные места; точная сумма появится после выбора дат.\n"
        f"🏠 Выберите подходящий номер:",
        reply_markup=kb,
    )
    logging.info(
        f"Пользователь {message.from_user.id} выбирает номер для {total_people} человек на {duration} суток"
    )
    await state.set_state(BookingStates.waiting_for_room_type)


@router.callback_query(F.data.regexp(r"view_room_(.+)"))
async def view_room(callback: CallbackQuery, state: FSMContext, bot: Bot):
    room_name = callback.data.split("_")[2]
    room = session.query(Room).filter_by(name=room_name).first()
    if not room:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад", callback_data="rooms_description"
                    )
                ]
            ]
        )
        try:
            await callback.message.edit_text("⚠️ Номер не найден.", reply_markup=kb)
        except TelegramBadRequest as e:
            if "there is no text in the message to edit" in str(e):
                await callback.message.delete()
                await callback.message.answer("⚠️ Номер не найден.", reply_markup=kb)
            else:
                raise
        logging.info(
            f"Пользователь {callback.from_user.id} попытался просмотреть несуществующую комнату {room_name}"
        )
        await callback.answer()
        return

    folder_map = {"2-местный": "2_room", "4-местный": "4_room", "6-местный": "6_room", "Домик на 2 места": "2_urta"}
    folder = folder_map.get(room.name)
    photo_dir = f"photos/{folder}" if folder else None
    photos = []
    if photo_dir and os.path.exists(photo_dir):
        photos = [
            os.path.join(photo_dir, f)
            for f in os.listdir(photo_dir)
            if f.lower().endswith((".jpg", ".png"))
        ]
        photos.sort()  # Сортируем для предсказуемого порядка

    text = get_room_description_text(room)
    await state.set_state(BookingStates.viewing_photos)
    await state.update_data(
        photo_folder=folder, photo_index=0, photo_list=photos, current_room=room.name
    )

    try:
        if photos:
            photo_name = os.path.basename(photos[0]).lower()
            logging.info(f"Попытка загрузки фото комнаты {room.name}: {photo_name}")
            file_id = await get_file_id_with_fallback(bot, photos[0], callback.from_user.id)
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад", callback_data="prev_photo"
                        ),
                        InlineKeyboardButton(
                            text="➡️ Вперед", callback_data="next_photo"
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            text="⬅️ К списку комнат", callback_data="rooms_description"
                        )
                    ],
                ]
            )
            await callback.message.edit_media(
                media=InputMediaPhoto(
                    media=file_id,
                    caption=f"{text}\n\nФото 1/{len(photos)}",
                    parse_mode="Markdown",
                ),
                reply_markup=kb,
            )
            logging.info(
                f"Пользователь {callback.from_user.id} просмотрел первую фотографию {room.name}"
            )
        else:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ К списку комнат", callback_data="rooms_description"
                        )
                    ]
                ]
            )
            try:
                await callback.message.edit_text(
                    f"{text}\n\n⚠️ Фотографии номера недоступны.",
                    parse_mode="Markdown",
                    reply_markup=kb,
                )
            except TelegramBadRequest as e:
                if "there is no text in the message to edit" in str(e):
                    await callback.message.delete()
                    await callback.message.answer(
                        f"{text}\n\n⚠️ Фотографии номера недоступны.",
                        parse_mode="Markdown",
                        reply_markup=kb,
                    )
                else:
                    raise
            logging.info(
                f"Пользователь {callback.from_user.id} просмотрел описание {room.name}, но фотографии отсутствуют"
            )
    except Exception as e:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ К списку комнат", callback_data="rooms_description"
                    )
                ]
            ]
        )
        try:
            await callback.message.edit_text(
                f"{text}\n\n⚠️ Ошибка при загрузке фото: {str(e)}",
                parse_mode="Markdown",
                reply_markup=kb,
            )
        except TelegramBadRequest as e:
            if "there is no text in the message to edit" in str(e):
                await callback.message.delete()
                await callback.message.answer(
                    f"{text}\n\n⚠️ Ошибка при загрузке фото: {str(e)}",
                    parse_mode="Markdown",
                    reply_markup=kb,
                )
            else:
                raise
        logging.error(
            f"Ошибка в view_room для {room.name}, пользователь {callback.from_user.id}: {str(e)}"
        )
    await callback.answer()


@router.callback_query(
    F.data.regexp(r"room_(\w+)"), StateFilter(BookingStates.waiting_for_room_type)
)
async def handle_room_selection(callback: CallbackQuery, state: FSMContext):
    try:
        logging.debug(
            f"Обработка callback: data={callback.data}, state={await state.get_state()}"
        )
        room_type = callback.data.split("_")[1]
        data = await state.get_data()
        duration = data.get("duration", 1)
        adults = data.get("adults", 1)
        children = data.get("children", 0)
        children_beds = data.get("children_beds", [])
        children_needing_beds = sum(children_beds)
        total_people = adults + children_needing_beds

        logging.info(
            f"Пользователь {callback.from_user.id} выбрал номер {room_type} для {total_people} человек"
        )

        await state.update_data(room_type=room_type)

        # Получаем текущий год из базы
        with session as db_session:
            booking_year = db_session.query(BookingYear).first()
            year = booking_year.year if booking_year else 2026
        logging.debug(f"handle_room_selection: year={year} from BookingYear")
        month = 6
        booked_dates = await get_booked_dates(room_type)
        logging.debug(
            f"Кэшированные даты для {room_type}: occupied={[d.isoformat() for d in booked_dates[0]]}, checkin={[d.isoformat() for d in booked_dates[1]]}"
        )

        calendar = await generate_calendar(
            year=year,
            month=month,
            booked_dates=booked_dates,
            min_date=date.today(),
            room_type=room_type,
            duration=duration,
        )

        await callback.message.edit_text(
            f"📅 Выберите дату заезда для номера {room_type_names[room_type]} в {year} году:\n"
            f"🟢 — свободные даты для заезда\n"
            f"🔴 — занятые даты\n"
            f"🔒 — даты в прошлом",
            reply_markup=calendar,
        )
        await state.set_state(BookingStates.waiting_for_start_date)
        logging.info(
            f"Пользователь {callback.from_user.id} перешёл к выбору даты заезда для {room_type}"
        )
        await callback.answer()
    except Exception as e:
        logging.error(
            f"Ошибка в handle_room_selection для пользователя {callback.from_user.id}: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.message.answer(
            "⚠️ Произошла ошибка при выборе номера. Попробуйте снова или свяжитесь с поддержкой."
        )
        await state.clear()
        await callback.message.answer("📅 Введите количество суток:")
        await state.set_state(BookingStates.waiting_for_duration)
        await callback.answer()


@router.message(Command("admin"))
async def admin_panel(message: Message, state: FSMContext = None):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        await message.answer("У вас нет доступа к этой команде.")
        return

    # Получаем текущий год и статус сезона
    booking_year = session.query(BookingYear).first()
    current_year = booking_year.year if booking_year else 2025  # Значение по умолчанию
    season = session.query(BookingSeason).first()
    season_status = "Активен" if season and season.is_active else "Завершён"
    if state:
        await state.clear()
    await message.answer(
        "👨‍💼 Панель администратора\n\n"
        f"📅 Год бронирования: {current_year}\n"
        f"🌞 Сезон: {season_status}\n\n"
        "Выберите действие кнопками ниже.",
        reply_markup=admin_menu_keyboard(),
    )
    session.add(AdminLog(admin_id=message.from_user.id, action="Открыл админ-панель"))
    session.commit()
    return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Активные заявки", callback_data="admin_view_bookings"
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f4ac \u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u044f \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0438", callback_data="admin_support_tickets"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Календарь бронирований",
                    callback_data="admin_view_calendar",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Статистика за год", callback_data="admin_select_stats_year"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📈 Изменить цены", callback_data="admin_change_prices"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Управление отзывами", callback_data="admin_manage_reviews"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📰 Создать новость", callback_data="admin_create_news"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💾 Скачать базу данных", callback_data="admin_download_db"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Скачать лог", callback_data="admin_download_log"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📅 Изменить год бронирования ({current_year})",
                    callback_data="admin_change_booking_year",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🌞 Управление сезоном ({season_status})",
                    callback_data="admin_manage_season",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Пользователи с согласием", callback_data="admin_view_users"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
        ]
    )
    await message.answer("👨‍💼 Панель администратора:", reply_markup=kb)
    session.add(AdminLog(admin_id=message.from_user.id, action="Открыл админ-панель"))
    session.commit()


@router.callback_query(F.data.startswith("admin_view_bookings_room_"))
async def admin_view_bookings_room(callback: CallbackQuery, state: FSMContext):
    from config import room_type_names, ADMIN_CHAT_ID, PRICE_PER_ADULT, ALL_ROOMS_PRICE

    try:
        if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
            await callback.answer("У вас нет прав на это действие", show_alert=True)
            return
        room_type = callback.data.split("_")[-1]
        room_name = room_type_names.get(room_type, room_type)

        await state.update_data(previous_menu="admin_view_bookings")

        try:
            await callback.message.delete()
        except TelegramBadRequest as e:
            logging.warning(f"Не удалось удалить сообщение: {str(e)}")

        bookings = (
            session.query(Booking)
            .filter(
                Booking.room_type == room_type,
                Booking.status.in_(ACTIVE_BOOKING_STATUSES),
            )
            .order_by(Booking.date_from.asc())
            .all()
        )

        if not bookings:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅ Назад", callback_data="admin_view_bookings"
                        )
                    ]
                ]
            )
            await callback.message.answer(
                f"📋 Нет активных заявок для номера: {room_name}", reply_markup=keyboard
            )
            session.add(
                AdminLog(
                    admin_id=callback.from_user.id,
                    action=f"Просмотрел заявки для номера {room_name}",
                )
            )
            session.commit()
            logging.info(
                f"Админ {callback.from_user.id} просмотрел заявки для номера {room_name}: count=0"
            )
            await callback.answer()
            return

        for idx, booking in enumerate(bookings):
            status = {
                "new": "🟡 Ожидает подтверждения администратора, пожалуйста, ожидайте",
                "pending": "🟡 Ожидает подтверждения",
                "awaiting_payment": "💰 Ожидает оплаты",
                "paid": "🟢 Оплачено, ждём в гости",
                "awaiting_cancellation": "⛔ Ожидает отмены",
            }[booking.status]
            nights = (booking.date_to - booking.date_from).days
            children_beds = get_children_beds(booking)
            children_needing_beds = sum(children_beds)
            total_people = booking.adults + children_needing_beds
            total = await calculate_revenue(
                booking.room_type, total_people, booking.date_from, booking.date_to
            )
            report_text = (
                f"📌 Заявка #{booking.id}\n"
                f"👤 {booking.full_name} (@{booking.username or 'без ника'})\n"
                f"📞 {booking.phone}\n"
                f"🏠 {room_type_names[booking.room_type]}\n"
                f"📅 Заезд: {booking.date_from.strftime('%d.%m.%Y')} после 14:00\n"
                f"📅 Выезд: {booking.date_to.strftime('%d.%m.%Y')} до 12:00\n"
                f"👨‍👩‍👧‍👦 Всего человек: {total_people} (взрослых: {booking.adults}, детей: {booking.children}, из них {children_needing_beds} с местами)\n"
                f"💰 Сумма: {total}₽\n"
                f"💬 Комментарий клиента: {booking.comment}\n"
                f"📝 Комментарий админа: {booking.admin_comment or 'Отсутствует'}\n"
                f"Статус: {status}\n"
            )

            buttons = []
            if booking.status == "new":
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text="✅ Подтвердить",
                            callback_data=f"admin_confirm_{booking.id}",
                        ),
                        InlineKeyboardButton(
                            text="❌ Отклонить",
                            callback_data=f"admin_reject_{booking.id}",
                        ),
                    ]
                )
            elif booking.status == "pending" or booking.status == "awaiting_payment":
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text="💸 Оплата получена",
                            callback_data=f"admin_paid_{booking.id}",
                        )
                    ]
                )
            elif booking.status == "awaiting_cancellation":
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text="✅ Подтвердить отмену",
                            callback_data=f"admin_confirm_cancel_{booking.id}",
                        ),
                        InlineKeyboardButton(
                            text="❌ Отклонить отмену",
                            callback_data=f"admin_reject_cancel_{booking.id}",
                        ),
                    ]
                )
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="💬 Ответить", callback_data=f"admin_reply_{booking.id}"
                    ),
                    InlineKeyboardButton(
                        text="🗑️ Удалить заявку",
                        callback_data=f"admin_delete_{booking.id}",
                    ),
                ]
            )
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="✍️ Добавить/изменить комментарий",
                        callback_data=f"admin_add_comment_{booking.id}",
                    )
                ]
            )

            if idx == len(bookings) - 1:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text="⬅ Назад", callback_data="admin_view_bookings"
                        )
                    ]
                )

            kb = InlineKeyboardMarkup(inline_keyboard=buttons)
            await callback.message.answer(
                report_text, parse_mode="HTML", reply_markup=kb
            )

        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Просмотрел заявки для номера {room_name}",
            )
        )
        session.commit()
        logging.info(
            f"Админ {callback.from_user.id} просмотрел заявки для номера {room_name}: count={len(bookings)}"
        )
        await callback.answer()
    except Exception as e:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅ Назад", callback_data="admin_view_bookings"
                    )
                ]
            ]
        )
        await callback.message.answer(f"❗ Произошла ошибка: {str(e)}", reply_markup=kb)
        logging.error(
            f"Ошибка в admin_view_bookings_room: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer()


@router.callback_query(F.data.startswith("admin_add_comment_"))
async def admin_add_comment(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    try:
        booking_id = int(callback.data.split("_")[3])
        booking = session.query(Booking).filter_by(id=booking_id).first()
        if not booking:
            await callback.answer(f"Заявка #{booking_id} не найдена.", show_alert=True)
            return

        await state.update_data(booking_id=booking_id)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🗑️ Очистить комментарий",
                        callback_data=f"admin_clear_comment_{booking_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data=f"admin_view_bookings_room_{booking.room_type}",
                    )
                ],
            ]
        )
        await callback.message.edit_text(
            f"📝 Текущий комментарий админа для заявки #{booking_id}:\n"
            f"{getattr(booking, 'admin_comment', 'Отсутствует')}\n\n"
            f"Введите новый комментарий или нажмите кнопку 'Очистить комментарий' для удаления текущего:",
            reply_markup=kb,
        )
        await state.set_state(BookingStates.waiting_for_admin_comment)
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Начал добавление/изменение комментария для заявки #{booking_id}",
            )
        )
        session.commit()
        logging.info(
            f"Админ {callback.from_user.id} начал добавление/изменение комментария для заявки #{booking_id}"
        )
    except Exception as e:
        logging.error(f"Ошибка в admin_add_comment: {str(e)}\n{traceback.format_exc()}")
        await callback.message.edit_text(
            f"❗ Ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад", callback_data="admin_view_bookings"
                        )
                    ]
                ]
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_clear_comment_"))
async def admin_clear_comment(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    try:
        booking_id = int(callback.data.split("_")[3])
        booking = session.query(Booking).filter_by(id=booking_id).first()
        if not booking:
            await callback.answer(f"Заявка #{booking_id} не найдена.", show_alert=True)
            return

        booking.admin_comment = None
        session.commit()

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data=f"admin_view_bookings_room_{booking.room_type}",
                    )
                ]
            ]
        )
        await callback.message.edit_text(
            f"✅ Комментарий админа для заявки #{booking_id} удалён.", reply_markup=kb
        )
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Удалил комментарий для заявки #{booking_id}",
            )
        )
        session.commit()
        logging.info(
            f"Админ {callback.from_user.id} удалил комментарий для заявки #{booking_id}"
        )
    except Exception as e:
        logging.error(
            f"Ошибка в admin_clear_comment: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.message.edit_text(
            f"❗ Ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад", callback_data="admin_view_bookings"
                        )
                    ]
                ]
            ),
        )
    await callback.answer()
    await state.clear()


@router.message(BookingStates.waiting_for_admin_comment)
async def process_admin_comment(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        await message.answer("У вас нет прав на это действие.")
        return

    try:
        data = await state.get_data()
        booking_id = data.get("booking_id")
        booking = session.query(Booking).filter_by(id=booking_id).first()
        if not booking:
            await message.answer("Заявка не найдена.")
            await state.clear()
            return

        comment = message.text.strip()
        if not comment:
            await message.answer("⚠️ Комментарий не может быть пустым. Введите текст:")
            return

        booking.admin_comment = comment
        session.commit()
        await state.update_data(admin_comment=booking.admin_comment)

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅️ Назад",
                        callback_data=f"admin_view_bookings_room_{booking.room_type}",
                    )
                ]
            ]
        )
        await message.answer(
            f"✅ Комментарий для заявки #{booking_id} сохранён.", reply_markup=kb
        )
        session.add(
            AdminLog(
                admin_id=message.from_user.id,
                action=f"Добавил/изменил комментарий для заявки #{booking_id}",
            )
        )
        session.commit()
        logging.info(
            f"Админ {message.from_user.id} добавил/изменил комментарий для заявки #{booking_id}: {comment}"
        )
    except Exception as e:
        logging.error(
            f"Ошибка в process_admin_comment: {str(e)}\n{traceback.format_exc()}"
        )
        await message.answer(f"❗ Ошибка: {str(e)}")
        await state.set_state(BookingStates.waiting_for_admin_comment)
    finally:
        await state.clear()


@router.callback_query(F.data.regexp(r"admin_delete_(\d+)"))
async def delete_booking_callback(callback: CallbackQuery):
    from config import ADMIN_CHAT_ID, ADMIN_USERNAME
    from utils import clear_booked_dates_cache

    try:
        if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
            await callback.answer("У вас нет прав на это действие", show_alert=True)
            return
        booking_id = int(callback.data.split("_")[2])
        booking = session.query(Booking).filter_by(id=booking_id).first()
        if not booking:
            await callback.answer(f"Заявка #{booking_id} не найдена.", show_alert=True)
            return
        booking.status = BookingStatus.CANCELED.value
        session.commit()
        clear_booked_dates_cache()  # Очищаем кэш, чтобы освободить даты
        try:
            if booking.user_id:
                await callback.message.bot.send_message(
                    chat_id=booking.user_id,
                    text=(
                        f"❌ Ваша заявка #{booking.id} была отменена администратором.\n"
                        f"Для уточнения деталей свяжитесь с {ADMIN_USERNAME}"
                    ),
                )
        except Exception as e:
            await callback.message.answer(
                f"❗ Ошибка при уведомлении пользователя: {e}"
            )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"Заявка #{booking.id} удалена 🗑️")
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Удалил заявку #{booking_id}",
                booking_id=booking_id,
            )
        )
        session.commit()
        logging.info(f"Админ {callback.from_user.id} удалил заявку #{booking.id}")
        await callback.answer()
    except ValueError:
        await callback.answer("Неверный формат данных заявки.", show_alert=True)
    except Exception as e:
        logging.error(
            f"Ошибка в delete_booking_callback: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer(f"Произошла ошибка: {str(e)}", show_alert=True)


async def send_booking_reminders(bot: Bot):
    logging.info("Задача send_booking_reminders запущена")
    while True:
        try:
            today = datetime.now().date()
            tomorrow = today + timedelta(days=1)
            bookings = (
                session.query(Booking)
                .filter(Booking.status == BookingStatus.PAID.value, Booking.date_from == tomorrow)
                .all()
            )
            for booking in bookings:
                children_beds = get_children_beds(booking)
                total_people = booking.adults + sum(children_beds)
                total = await calculate_revenue(
                    booking.room_type, total_people, booking.date_from, booking.date_to
                )
                reminder_text = (
                    f"📅 Напоминание о бронировании #{booking.id}\n"
                    f"👤 {booking.full_name} (@{booking.username or 'без ника'})\n"
                    f"📞 {booking.phone}\n"
                    f"🏠 {room_type_names[booking.room_type]}\n"
                    f"📅 Заезд: {booking.date_from.strftime('%d.%m.%Y')} после 14:00\n"
                    f"📅 Выезд: {booking.date_to.strftime('%d.%m.%Y')} до 12:00\n"
                    f"👨‍👩‍👧‍👦 Взрослых: {booking.adults}, Детей: {booking.children}\n"
                    f"💰 Сумма: {total}₽\n"
                    f"🏠 Адрес: {HOTEL_ADDRESS}\n"
                    f"📞 Для связи: {ADMIN_CONTACT}\n"
                    f"🗺 Местоположение: {LOCATION_LINK}"
                )
                # Уведомление гостю
                try:
                    await bot.send_message(
                        booking.user_id, reminder_text, parse_mode="HTML"
                    )
                    logging.info(
                        f"Отправлено напоминание гостю {booking.user_id} о заявке #{booking.id}"
                    )
                except Exception as e:
                    logging.error(
                        f"Ошибка при отправке напоминания гостю {booking.user_id} для заявки #{booking.id}: {str(e)}"
                    )
                    await bot.send_message(
                        ADMIN_CHAT_ID,
                        f"❗ Ошибка при отправке напоминания гостю {booking.user_id} для заявки #{booking.id}: {str(e)}",
                    )
                # Уведомление админу
                try:
                    await bot.send_message(
                        ADMIN_CHAT_ID,
                        f"📅 Напоминание: гость заезжает завтра по заявке #{booking.id}\n\n{reminder_text}",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(
                            inline_keyboard=[
                                [
                                    InlineKeyboardButton(
                                        text="💬 Ответить",
                                        callback_data=f"admin_reply_{booking.id}",
                                    )
                                ]
                            ]
                        ),
                    )
                    logging.info(
                        f"Отправлено напоминание админу о заявке #{booking.id}"
                    )
                except Exception as e:
                    logging.error(
                        f"Ошибка при отправке напоминания админу для заявки #{booking.id}: {str(e)}"
                    )
                    await bot.send_message(
                        ADMIN_CHAT_ID,
                        f"❗ Ошибка при отправке напоминания админу для заявки #{booking.id}: {str(e)}",
                    )
                session.add(
                    AdminLog(
                        admin_id=ADMIN_CHAT_ID,
                        action=f"Отправлено напоминание о заезде по заявке #{booking.id}",
                        booking_id=booking.id,
                    )
                )
                session.commit()
        except Exception as e:
            logging.error(f"Ошибка в send_booking_reminders: {str(e)}")
            await bot.send_message(
                ADMIN_CHAT_ID, f"❗ Ошибка в задаче send_booking_reminders: {str(e)}"
            )
        await asyncio.sleep(86400)  # Проверять раз в сутки (24 часа)


@router.callback_query(F.data == "admin")
async def admin_callback(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет доступа к этой команде.", show_alert=True)
        return
    await state.update_data(previous_menu="main_menu")

    # Получаем текущий год и статус сезона
    booking_year = session.query(BookingYear).first()
    current_year = booking_year.year if booking_year else 2025
    season = session.query(BookingSeason).first()
    season_status = "Активен" if season and season.is_active else "Завершён"
    await state.clear()
    await safe_edit_or_answer(
        callback.message,
        "👨‍💼 Панель администратора\n\n"
        f"📅 Год бронирования: {current_year}\n"
        f"🌞 Сезон: {season_status}\n\n"
        "Выберите действие кнопками ниже.",
    )
    await show_reply_keyboard_message(
        callback.message,
        "Админские кнопки доступны снизу.",
        admin_menu_keyboard(),
    )
    session.add(AdminLog(admin_id=callback.from_user.id, action="Открыл админ-панель"))
    session.commit()
    await callback.answer()
    return

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Активные заявки", callback_data="admin_view_bookings"
                )
            ],
            [
                InlineKeyboardButton(
                    text="\U0001f4ac \u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u044f \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0438", callback_data="admin_support_tickets"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Календарь бронирований",
                    callback_data="admin_view_calendar",
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Статистика за год", callback_data="admin_select_stats_year"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📈 Изменить цены", callback_data="admin_change_prices"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⭐ Управление отзывами", callback_data="admin_manage_reviews"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📰 Создать новость", callback_data="admin_create_news"
                )
            ],
            [
                InlineKeyboardButton(
                    text="💾 Скачать базу данных", callback_data="admin_download_db"
                )
            ],
            [
                InlineKeyboardButton(
                    text="📄 Скачать лог", callback_data="admin_download_log"
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"📅 Изменить год бронирования ({current_year})",
                    callback_data="admin_change_booking_year",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"🌞 Управление сезоном ({season_status})",
                    callback_data="admin_manage_season",
                )
            ],
            [
                InlineKeyboardButton(
                    text="👥 Пользователи с согласием", callback_data="admin_view_users"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
        ]
    )
    await callback.message.edit_text("👨‍💼 Панель администратора:", reply_markup=kb)
    session.add(AdminLog(admin_id=callback.from_user.id, action="Открыл админ-панель"))
    session.commit()
    await callback.answer()


@router.callback_query(F.data == "admin_select_stats_year")
async def admin_select_stats_year(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    await state.update_data(previous_menu="admin")

    # Получаем текущий год
    booking_year = session.query(BookingYear).first()
    current_year = booking_year.year if booking_year else 2025
    # Формируем список доступных годов (2025–2030)
    allowed_years = range(2025, 2031)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"{year}", callback_data=f"view_stats_year_{year}"
                )
            ]
            for year in allowed_years
        ]
        + [[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]]
    )

    await callback.message.edit_text(
        f"📅 Выберите год для просмотра статистики:", reply_markup=kb
    )
    session.add(
        AdminLog(
            admin_id=callback.from_user.id, action="Открыл выбор года для статистики"
        )
    )
    session.commit()
    await callback.answer()


@router.callback_query(F.data == "admin_change_booking_year")
async def admin_change_booking_year(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    await state.update_data(previous_menu="admin")

    current_year = session.query(BookingYear).first().year
    next_year = current_year + 1
    prev_year = current_year - 1 if current_year > 2025 else current_year

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"Установить {prev_year}",
                    callback_data=f"set_booking_year_{prev_year}",
                )
            ],
            [
                InlineKeyboardButton(
                    text=f"Установить {next_year}",
                    callback_data=f"set_booking_year_{next_year}",
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")],
        ]
    )
    await callback.message.edit_text(
        f"📅 Текущий год бронирования: {current_year}\nВыберите новый год:",
        reply_markup=kb,
    )
    session.add(
        AdminLog(
            admin_id=callback.from_user.id, action="Открыл изменение года бронирования"
        )
    )
    session.commit()
    await callback.answer()


@router.callback_query(F.data.regexp(r"set_booking_year_(\d+)"))
async def set_booking_year(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    new_year = int(callback.data.split("_")[3])
    try:
        booking_year = session.query(BookingYear).first()
        booking_year.year = new_year
        session.commit()
        await callback.message.edit_text(
            f"✅ Год бронирования изменён на {new_year}.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                ]
            ),
        )
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Изменил год бронирования на {new_year}",
            )
        )
        session.commit()
        logging.info(
            f"Админ {callback.from_user.id} изменил год бронирования на {new_year}"
        )
    except Exception as e:
        logging.error(f"Ошибка при изменении года бронирования: {str(e)}")
        await callback.message.edit_text(
            f"❗ Ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                ]
            ),
        )
    await callback.answer()


@router.callback_query(F.data == "admin_manage_season")
async def admin_manage_season(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    await state.update_data(previous_menu="admin")

    season = session.query(BookingSeason).first()
    action_text = "Завершить сезон" if season.is_active else "Начать сезон"
    action_data = "end_season" if season.is_active else "start_season"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=action_text, callback_data=action_data)],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")],
        ]
    )
    await callback.message.edit_text(
        f"🌞 Текущий статус сезона: {'Активен' if season.is_active else 'Завершён'}\nВыберите действие:",
        reply_markup=kb,
    )
    session.add(
        AdminLog(admin_id=callback.from_user.id, action="Открыл управление сезоном")
    )
    session.commit()
    await callback.answer()


@router.callback_query(F.data.in_(["start_season", "end_season"]))
async def toggle_season(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    action = callback.data
    try:
        season = session.query(BookingSeason).first()
        if action == "start_season":
            season.is_active = True
            status = "начат"
        else:
            season.is_active = False
            status = "завершён"
        session.commit()
        await callback.message.edit_text(
            f"✅ Сезон бронирования {status}.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                ]
            ),
        )
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"{status.capitalize()} сезон бронирования",
            )
        )
        session.commit()
        logging.info(f"Админ {callback.from_user.id} {status} сезон бронирования")
    except Exception as e:
        logging.error(f"Ошибка при управлении сезоном: {str(e)}")
        await callback.message.edit_text(
            f"❗ Ошибка: {str(e)}",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                ]
            ),
        )
    await callback.answer()


@router.callback_query(F.data == "admin_manage_reviews")
async def admin_manage_reviews(callback: CallbackQuery):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    try:
        reviews = session.query(Review).order_by(Review.created_at.desc()).all()
        if not reviews:
            await callback.message.answer(
                "Отзывов пока нет.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                    ]
                ),
            )
            return

        def escape_markdown(text: str) -> str:
            """Экранирование специальных символов для MarkdownV2."""
            if not text:
                return "Аноним"
            chars = [
                "_",
                "*",
                "[",
                "]",
                "(",
                ")",
                "~",
                "`",
                ">",
                "#",
                "+",
                "-",
                "=",
                "|",
                "{",
                "}",
                ".",
                "!",
            ]
            for char in chars:
                text = text.replace(char, f"\\{char}")
            return text

        for review in reviews:
            booking = session.query(Booking).filter_by(id=review.booking_id).first()
            if not booking:
                continue  # Пропускаем, если заявка не найдена
            username = escape_markdown(booking.username or "Аноним")
            comment = escape_markdown(review.comment or "Без комментария")
            room_type = escape_markdown(
                room_type_names[booking.room_type]
            )  # Экранируем название номера
            created_at = escape_markdown(
                review.created_at
                if isinstance(review.created_at, str)
                else review.created_at.strftime("%Y-%m-%d %H:%M:%S")
            )
            text = (
                f"📌 *Отзыв к заявке* `#{review.booking_id}`\n"
                f"👤 @{username}\n"
                f"🏠 {room_type}\n"
                f"⭐ *Рейтинг*: {'★' * review.rating}{'☆' * (5 - review.rating)}\n"
                f"💬 {comment}\n"
                f"📅 {created_at}"
            )
            await callback.message.answer(
                text,
                parse_mode="MarkdownV2",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🗑️ Удалить отзыв",
                                callback_data=f"admin_delete_review_{review.id}",
                            )
                        ],
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")],
                    ]
                ),
            )

        session.add(
            AdminLog(
                admin_id=callback.from_user.id, action="Просмотрел управление отзывами"
            )
        )
        session.commit()
        logging.info(
            f"Админ {callback.from_user.id} просмотрел управление отзывами: count={len(reviews)}"
        )
    except Exception as e:
        logging.error(
            f"Ошибка в admin_manage_reviews: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.message.answer(f"❗ Ошибка: {str(e)}")
    await callback.answer()


@router.callback_query(F.data.startswith("admin_delete_review_"))
async def admin_delete_review(callback: CallbackQuery):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    try:
        review_id = int(callback.data.split("_")[3])
        review = session.query(Review).filter_by(id=review_id).first()
        if not review:
            await callback.answer(f"Отзыв #{review_id} не найден.", show_alert=True)
            return
        session.delete(review)
        session.commit()
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"Отзыв #{review_id} удалён 🗑️")
        session.add(
            AdminLog(
                admin_id=callback.from_user.id, action=f"Удалил отзыв #{review_id}"
            )
        )
        session.commit()
        logging.info(f"Админ {callback.from_user.id} удалил отзыв #{review_id}")
    except ValueError:
        await callback.answer("Неверный формат данных отзыва.", show_alert=True)
    except Exception as e:
        logging.error(
            f"Ошибка в admin_delete_review: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer(f"❗ Ошибка: {str(e)}", show_alert=True)
    await callback.answer()


@router.callback_query(F.data == "book")
async def process_booking(callback: types.CallbackQuery, state: FSMContext):
    from utils import (
        format_booking_info,
    )  # Импорт внутри функции для избежания циклического импорта

    user_id = callback.from_user.id
    logging.debug(f"Пользователь {user_id} начал процесс бронирования")
    await send_reply_keyboard(callback.message, compact_main_keyboard())

    season = session.query(BookingSeason).first()
    if not season.is_active:
        await callback.message.edit_text(
            "К сожалению, сезон бронирования окончен, ждём вас в следующем году!",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_main")]
                ]
            ),
        )
        logging.info(
            f"Пользователь {user_id} попытался забронировать, но сезон завершён"
        )
        await callback.answer()
        return

    active_booking = (
        session.query(Booking)
        .filter(
            Booking.user_id == user_id,
            Booking.status.in_(
                ["new", "pending", "awaiting_payment", "awaiting_payment_confirmation"]
            ),
        )
        .order_by(Booking.id.desc())
        .first()
    )

    if active_booking:
        status_mapping = {
            "new": "🟡 Ожидает подтверждения администратора, пожалуйста, ожидайте",
            "pending": "🟡 Ожидает подтверждения",
            "awaiting_payment": "💰 Ожидает оплаты",
            "awaiting_payment_confirmation": "📸 Ожидает подтверждения оплаты",
        }
        status_text = status_mapping.get(active_booking.status, active_booking.status)

        booking_info = (
            f"📌 У вас уже есть активная заявка #{active_booking.id}:\n"
            f"{await format_booking_info(active_booking)}\n"
            f"Статус: {status_text}\n"
        )

        buttons = []
        if active_booking.status in ["new", "pending"]:
            buttons = [
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
            ]
        elif active_booking.status == "awaiting_payment":
            buttons = [
                [
                    InlineKeyboardButton(
                        text="📸 Отправить скриншот",
                        callback_data=f"send_screenshot_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
            ]
        elif active_booking.status == "awaiting_payment_confirmation":
            buttons = [
                [
                    InlineKeyboardButton(
                        text="📸 Отправить скриншот",
                        callback_data=f"send_screenshot_{active_booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Связаться с администратором",
                        callback_data=f"start_payment_support_{active_booking.id}",
                    )
                ],
            ]

        buttons.append(
            [InlineKeyboardButton(text="⬅ Назад", callback_data="back_to_main")]
        )
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)

        await callback.message.edit_text(
            booking_info
            + "\nПожалуйста, завершите действия с текущей заявкой, прежде чем создавать новую.",
            reply_markup=kb,
            parse_mode="HTML",
        )
        logging.info(
            f"Пользователь {user_id} попытался создать новую заявку, но уже имеет активную заявку #{active_booking.id} в статусе {active_booking.status}"
        )
        await callback.answer()
        return

    draft = get_booking_draft(user_id)
    if draft:
        await show_booking_resume_prompt(callback, draft)
        await callback.answer()
        return

    await state.clear()
    await callback.message.edit_text("📅 На сколько суток вы хотите заехать?")
    await state.set_state(BookingStates.waiting_for_duration)
    logging.info(f"Пользователь {user_id} начал бронирование")
    await callback.answer()


@router.callback_query(F.data == "new_booking_ignore_draft")
async def new_booking_ignore_draft(callback: CallbackQuery, state: FSMContext):
    clear_booking_draft(callback.from_user.id)
    await state.clear()
    await callback.message.edit_text("📅 На сколько суток вы хотите заехать?")
    await state.set_state(BookingStates.waiting_for_duration)
    await callback.answer()


@router.callback_query(F.data == "resume_booking_draft")
async def resume_booking_draft(callback: CallbackQuery, state: FSMContext):
    draft = get_booking_draft(callback.from_user.id)
    if not draft:
        await callback.answer("Черновик не найден. Начните бронирование заново.", show_alert=True)
        await callback.message.edit_text("📅 На сколько суток вы хотите заехать?")
        await state.set_state(BookingStates.waiting_for_duration)
        return

    await state.clear()
    await state.update_data(**draft)
    data = await state.get_data()

    if not data.get("duration"):
        await callback.message.edit_text("📅 На сколько суток вы хотите заехать?")
        await state.set_state(BookingStates.waiting_for_duration)
    elif not data.get("adults"):
        await callback.message.edit_text("👨‍👩‍👧‍👦 Введите количество взрослых:")
        await state.set_state(BookingStates.waiting_for_adults)
    elif data.get("children") is None:
        await callback.message.edit_text("👶 Сколько детей будет проживать? (до 3х лет):")
        await state.set_state(BookingStates.waiting_for_children)
    elif not data.get("room_type"):
        await callback.message.edit_text("Продолжаем бронирование. Сейчас покажу подходящие номера.")
        await select_room_type(callback.message, state)
    elif not data.get("start_date"):
        room_type = data["room_type"]
        current_year = get_booking_year_value()
        booked_dates = await get_booked_dates(room_type)
        calendar = await generate_calendar(
            year=current_year,
            month=6,
            booked_dates=booked_dates,
            room_type=room_type,
            duration=int(data.get("duration", 1)),
            state=state,
        )
        await callback.message.edit_text(
            f"📅 Свободные даты заезда для варианта «{room_type_names.get(room_type, room_type)}» в {current_year} году:",
            reply_markup=calendar,
        )
        await state.set_state(BookingStates.waiting_for_start_date)
    else:
        start_date = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
        end_date = datetime.strptime(data["end_date"], "%Y-%m-%d").date()
        children_beds = data.get("children_beds", [])
        total_people = int(data.get("adults", 1)) + sum(children_beds)
        total = await calculate_revenue(data["room_type"], total_people, start_date, end_date)
        person_day_price_text = await get_person_day_price_text(
            data["room_type"], total_people, start_date, end_date
        )
        await state.update_data(total_price=total, user_id=callback.from_user.id)
        await callback.message.edit_text(
            "<b>Черновик восстановлен. Проверьте данные:</b>\n"
            f"Количество гостей: {total_people}\n"
            f"🏠 Номер: {room_type_names[data['room_type']]}\n"
            f"📅 Заезд: {start_date.strftime('%d.%m.%Y')} после 14:00\n"
            f"📅 Выезд: {end_date.strftime('%d.%m.%Y')} до 12:00 ({get_duration_text(data.get('duration', 1))})\n"
            f"{person_day_price_text}"
            f"💰 Сумма: {total}₽",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_booking")],
                    [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_booking")],
                ]
            ),
        )
        await state.set_state(BookingStates.confirm_booking)
    await callback.answer()


@router.callback_query(F.data == "about_us")
async def show_about_us(callback: CallbackQuery, state: FSMContext):
    # Сохраняем предыдущее состояние для кнопки "Назад"
    await state.update_data(previous_menu="main_menu")

    about_text = (
        "🏠 **Дача на Байкале — уютный отдых у озера!**\n\n"
        "Мы находимся в селе Максимиха — в живописном месте у озера Байкал, где вы сможете насладиться природой и спокойствием.\n\n"
        "👨‍👩‍👦‍👦 **Вместимость всего объекта — 14 человек:**\n"
        "- 6х местный номер.\n"
        "- 4х местный номер.\n"
        "- 2х местный номер.\n"
        "- Отдельный домик на 2 человека.\n\n"
        "🌳 **На территории:**\n"
        "- Благоустроенный туалет и душевая (общие).\n"
        "- Открытая кухня и обеденная зона (общие).\n"
        "- Беседка (в общем пользовании).\n"
        "- Качели, теннисный стол, баскетбольное кольцо, шезлонги.\n\n"
        "🛠 **Предоставляется:**\n"
        "- Постельное белье.\n"
        "- Мангал и шампуры.\n"
        "- Кухня и кухонная утварь.\n"
        "- Дрова (1 топка в день бесплатно, последующие — 250₽).\n\n"
        "📜 **Правила на территории:**\n"
        "- Пребывание посторонних лиц на участке недопустимо, о ваших гостях необходимо предупреждать!\n"
        "- Просим соблюдать чистоту и не разбрасывать вещи, особенно в обеденной зоне, так как кухня находится в общем пользовании.\n"
        "- Авто можно загнать на территорию после 20:00, а утром до 12:00 машину необходимо выгнать. Это правило действует ежедневно, так как территория небольшая, и парковку мы не предоставляем.\n"
        "- Исключение: если вы забронировали все номера, авто можно оставить на территории.\n"
    )
    await safe_edit_or_answer(
        callback.message,
        about_text,
        reply_markup=None,
    )
    await show_reply_keyboard_message(
        callback.message,
        "Выберите пункт раздела «О нас» кнопками ниже.",
        about_menu_keyboard(),
    )

    logging.info(f"Пользователь {callback.from_user.id} открыл раздел 'О нас'")
    await callback.answer()


@router.callback_query(F.data == "back_to_main")
async def back_to_main(callback: CallbackQuery, state: FSMContext):
    buttons = [
        [InlineKeyboardButton(text="📅 Забронировать номер", callback_data="book")],
        [InlineKeyboardButton(text="🔎 Проверить свободные даты", callback_data="check_availability")],
        [InlineKeyboardButton(text="📋 Мои заявки", callback_data="my_bookings")],
        [
            InlineKeyboardButton(
                text="⭐ Посмотреть отзывы", callback_data="view_reviews"
            )
        ],
        [InlineKeyboardButton(text="📞 Поддержка", callback_data="support")],
        [InlineKeyboardButton(text="ℹ️ О нас", callback_data="about_us")],
    ]
    if str(callback.from_user.id) == str(ADMIN_CHAT_ID):
        buttons.append(
            [InlineKeyboardButton(text="👨‍💼 Админ-панель", callback_data="admin")]
        )
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)

    new_text = "🌊 Главное меню системы бронирования Дача на Байкале 🌲"

    # Проверяем, изменился ли текст или клавиатура
    if callback.message.text == new_text:
        await send_reply_keyboard(
            callback.message,
            main_menu_keyboard(str(callback.from_user.id) == str(ADMIN_CHAT_ID)),
        )
        await callback.answer()
        return

    await safe_edit_or_answer(callback.message, new_text)
    await send_reply_keyboard(
        callback.message,
        main_menu_keyboard(str(callback.from_user.id) == str(ADMIN_CHAT_ID)),
    )
    await state.clear()
    logging.info(
        f"Пользователь {callback.from_user.id} вернулся в главное меню, состояние сброшено"
    )
    await callback.answer()


@router.callback_query(F.data == "rules_living")
async def show_rules_living(callback: CallbackQuery, state: FSMContext):
    # Сохраняем предыдущее состояние для кнопки "Назад"
    await state.update_data(previous_menu="about_us")

    text = (
        "📜 Правила проживания\n\n"
        "1. Заезд после 14:00, выезд до 12:00.\n"
        "2. Курение в номерах запрещено.\n"
        "3. Проживание с животными согласовывается заранее.\n"
        "4. Соблюдайте тишину после 23:00.\n"
        "5. Уборка номеров проводится по запросу.\n"
        "6. За порчу имущества взимается штраф."
    )
    try:
        await callback.message.edit_text(text, reply_markup=None)
    except TelegramBadRequest as e:
        if "there is no text in the message to edit" in str(e):
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=about_menu_keyboard())
        else:
            raise
    logging.info(f"Пользователь {callback.from_user.id} просмотрел правила проживания")
    await callback.answer()


@router.callback_query(F.data == "territory_overview")
async def show_territory_overview(callback: CallbackQuery, state: FSMContext, bot: Bot):
    # Сохраняем предыдущее состояние для кнопки "Назад"
    await state.update_data(previous_menu="about_us")

    general_text = "🌳 Обзор территории\n\n"
    photo_descriptions = {
        "photo1.jpg": "🏡 Схема расположения наших уютных номеров на территории гостевого дома.",
        "photo2.jpg": "🗺️ Подробная схема размещения номеров для вашего удобства.",
        "photo3.jpg": "🛋️ Комфортный шезлонг для отдыха на свежем воздухе с видом на природу.",
        "photo4.jpg": "🎠 Уютные качели для детей и взрослых — идеальное место для релакса!",
        "photo5.jpg": "🏓 Теннисный стол для активного отдыха и весёлых игр на свежем воздухе.",
        "photo6.jpg": "🍳 Просторная кухонная зона с видом на природу — готовьте с удовольствием!",
        "photo7.jpg": "🥗 Уютная кухня, где можно собраться всей семьёй за вкусным обедом.",
        "photo8.jpg": "🍴 Открытая кухонная зона.",
        "photo9.jpg": "☕ Открытая кухонная зона.",
        "photo10.jpg": "🍽️ Открытая кухонная зона.",
    }
    photo_dir = "photos/territory"
    photos = []

    # Проверяем наличие фотографий
    if os.path.exists(photo_dir):
        photos = [
            os.path.join(photo_dir, f)
            for f in os.listdir(photo_dir)
            if f.lower().endswith((".jpg", ".png"))
        ]
        # Сортируем фотографии в порядке ключей из photo_descriptions
        photos = sorted(
            photos,
            key=lambda x: list(photo_descriptions.keys()).index(os.path.basename(x)),
        )
    else:
        logging.warning(f"Папка {photo_dir} не найдена")

    if not photos:
        try:
            await callback.message.edit_text(
                f"{general_text}\n\n⚠️ Фотографии территории недоступны.",
                reply_markup=None,
            )
        except TelegramBadRequest as e:
            if "there is no text in the message to edit" in str(e):
                await callback.message.delete()
                await callback.message.answer(
                    f"{general_text}\n\n⚠️ Фотографии территории недоступны.",
                    reply_markup=about_menu_keyboard(),
                )
            else:
                raise
        logging.info(
            f"Пользователь {callback.from_user.id} просмотрел обзор территории, но фотографии отсутствуют"
        )
        await callback.answer()
        return

    logging.debug(f"Найдены фотографии: {[os.path.basename(p) for p in photos]}")
    await state.set_state(BookingStates.viewing_photos)
    await state.update_data(
        photo_folder="territory",
        photo_index=0,
        photo_list=photos,
        photo_descriptions=photo_descriptions,
        general_text=general_text,
    )

    try:
        photo_path = photos[0]
        photo_name = os.path.basename(photo_path).lower()
        logging.debug(f"Загрузка фото территории: {photo_name}")

        # Проверяем кэш перед загрузкой
        file_id = photo_file_id_cache.get(photo_path)
        if not file_id:
            file_id = await get_file_id_with_fallback(
                bot, photo_path, callback.from_user.id
            )

        description = photo_descriptions.get(photo_name, "")
        # Обновляем сообщение с первой фотографией
        try:
            await callback.message.edit_media(
                media=InputMediaPhoto(
                    media=file_id,
                    caption=f"{general_text}\n{description}\n\nФото 1/{len(photos)}",
                ),
                reply_markup=get_room_gallery_keyboard(False),
            )
        except (TelegramBadRequest, AttributeError):
            try:
                await callback.message.delete()
            except TelegramBadRequest:
                pass
            await callback.message.answer_photo(
                photo=file_id,
                caption=f"{general_text}\n{description}\n\nФото 1/{len(photos)}",
                reply_markup=get_room_gallery_keyboard(False),
            )

        logging.info(
            f"Пользователь {callback.from_user.id} просмотрел первую фотографию территории: {photo_name}, описание: {description}"
        )
    except Exception as e:
        try:
            await callback.message.edit_text(
                f"{general_text}\n\n⚠️ Ошибка при загрузке фото: {str(e)}",
                reply_markup=None,
            )
        except TelegramBadRequest as e:
            if "there is no text in the message to edit" in str(e):
                await callback.message.delete()
                await callback.message.answer(
                    f"{general_text}\n\n⚠️ Ошибка при загрузке фото: {str(e)}",
                    reply_markup=about_menu_keyboard(),
                )
            else:
                raise
        logging.error(
            f"Ошибка в show_territory_overview для пользователя {callback.from_user.id}: {str(e)}\n{traceback.format_exc()}"
        )

    await callback.answer()


@router.callback_query(F.data == "booking_rules")
async def show_booking_rules(callback: CallbackQuery, state: FSMContext):
    # Сохраняем предыдущее состояние для кнопки "Назад"
    await state.update_data(previous_menu="about_us")

    text = (
        "📅 **Правила бронирования (пошагово)**\n\n"
        "1. Выберите дату заезда и количество суток через бота.\n"
        "2. Укажите количество взрослых и детей (до 3 лет — бесплатно).\n"
        "3. Выберите подходящий номер из доступных.\n"
        "4. Подтвердите заявку и отправьте свои контактные данные.\n"
        "5. Дождитесь подтверждения от администратора (в течение 24 часов).\n"
        "6. После подтверждения оплатите бронирование в течение 3 часов.\n"
        "7. Получите уведомление о бронировании с деталями заезда.\n\n"
        "ℹ️ **Важно:**\n"
        "- Бронирование доступно минимум за 1 день до заезда.\n"
        "- Для групп более 14 человек свяжитесь с администратором."
    )
    try:
        await callback.message.edit_text(text, reply_markup=None)
    except TelegramBadRequest as e:
        if "there is no text in the message to edit" in str(e):
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=about_menu_keyboard())
        else:
            raise
    logging.info(
        f"Пользователь {callback.from_user.id} просмотрел правила бронирования"
    )
    await callback.answer()


@router.callback_query(F.data == "cancellation_rules")
async def show_cancellation_rules(callback: CallbackQuery, state: FSMContext):
    # Сохраняем предыдущее состояние для кнопки "Назад"
    await state.update_data(previous_menu="about_us")

    text = (
        "❌ **Правила отмены бронирования (пошагово)**\n\n"
        "1. Перейдите в раздел 'Мои заявки' и выберите нужную заявку.\n"
        "2. Нажмите 'Отменить заявку' и укажите причину отмены.\n"
        "3. Дождитесь рассмотрения запроса администратором (в течение 24 часов).\n"
        "4. Получите уведомление о подтверждении или отклонении отмены.\n"
        "5. При подтверждении возврат средств будет выполнен в течение 1 рабочего дня.\n\n"
        "ℹ️ **Важно:**\n"
        "- Отмену с возвратом оплаты можно сделать не ранее чем за 7 дней до даты заезда.\n"
        "- При отмене менее чем за 7 дней удерживается 50% стоимости."
    )
    try:
        await callback.message.edit_text(text, reply_markup=None)
    except TelegramBadRequest as e:
        if "there is no text in the message to edit" in str(e):
            await callback.message.delete()
            await callback.message.answer(text, reply_markup=about_menu_keyboard())
        else:
            raise
    logging.info(f"Пользователь {callback.from_user.id} просмотрел правила отмены")
    await callback.answer()


@router.callback_query(F.data == "rooms_description")
async def show_rooms_description(callback: CallbackQuery, state: FSMContext, bot: Bot):
    # Сохраняем предыдущее состояние для кнопки "Назад"
    await state.update_data(previous_menu="about_us")

    rooms = session.query(Room).filter(Room.is_available == True).all()
    if not rooms:
        try:
            await callback.message.edit_text(
                "⚠️ Нет доступных номеров для просмотра.", reply_markup=None
            )
        except TelegramBadRequest as e:
            if "there is no text in the message to edit" in str(e):
                await callback.message.delete()
                await callback.message.answer(
                    "⚠️ Нет доступных номеров для просмотра.",
                    reply_markup=about_menu_keyboard(),
                )
            else:
                raise
        logging.info(
            f"Пользователь {callback.from_user.id} попытался просмотреть комнаты, но они отсутствуют"
        )
        await callback.answer()
        return

    room = rooms[0]
    folder_map = {
        "2-местный": "2_room",
        "2х местный номер": "2_room",
        "4-местный": "4_room",
        "4х местный номер": "4_room",
        "6-местный": "6_room",
        "6х местный номер": "6_room",
        "Домик на 2 места": "2_urta",
        "2х местный домик": "2_urta",
    }
    folder = folder_map.get(room.name)
    photo_dir = f"photos/{folder}" if folder else None
    photos = []
    if photo_dir and os.path.exists(photo_dir):
        photos = [
            os.path.join(photo_dir, f)
            for f in os.listdir(photo_dir)
            if f.endswith((".jpg", ".png"))
        ]
    text = get_room_description_text(room)
    await state.set_state(BookingStates.viewing_photos)
    await state.update_data(
        photo_folder=folder,
        photo_index=0,
        photo_list=photos,
        current_room=room.name,
        room_index=0,
        rooms=[r.name for r in rooms],
    )
    try:
        if photos:
            photo_name = os.path.basename(photos[0]).lower()
            logging.info(f"Попытка загрузки фото комнаты {room.name}: {photo_name}")
            file_id = await get_file_id_with_fallback(bot, photos[0], callback.from_user.id)
            try:
                await callback.message.edit_media(
                    media=InputMediaPhoto(
                        media=file_id,
                        caption=f"{text}\n\nФото 1/{len(photos)}",
                    ),
                    reply_markup=get_room_gallery_keyboard(True),
                )
            except (TelegramBadRequest, AttributeError):
                try:
                    await callback.message.delete()
                except TelegramBadRequest:
                    pass
                await callback.message.answer_photo(
                    photo=file_id,
                    caption=f"{text}\n\nФото 1/{len(photos)}",
                    reply_markup=get_room_gallery_keyboard(True),
                )
            logging.info(
                f"Пользователь {callback.from_user.id} просмотрел первую фотографию {room.name}"
            )
        else:
            try:
                await callback.message.edit_text(
                    f"{text}\n\n⚠️ Фотографии номера недоступны.",
                    reply_markup=None,
                )
            except TelegramBadRequest as e:
                if "there is no text in the message to edit" in str(e):
                    await callback.message.delete()
                    await callback.message.answer(
                        f"{text}\n\n⚠️ Фотографии номера недоступны.",
                        reply_markup=get_room_gallery_keyboard(True),
                    )
                else:
                    raise
            logging.info(
                f"Пользователь {callback.from_user.id} просмотрел описание {room.name}, но фотографии отсутствуют"
            )
    except Exception as e:
        try:
            await callback.message.edit_text(
                f"{text}\n\n⚠️ Ошибка при загрузке фото: {str(e)}",
                reply_markup=None,
            )
        except TelegramBadRequest as e:
            if "there is no text in the message to edit" in str(e):
                await callback.message.delete()
                await callback.message.answer(
                    f"{text}\n\n⚠️ Ошибка при загрузке фото: {str(e)}",
                    reply_markup=get_room_gallery_keyboard(True),
                )
            else:
                raise
        logging.error(
            f"Ошибка в show_rooms_description для {room.name}, пользователь {callback.from_user.id}: {str(e)}"
        )
    await callback.answer()


def get_room_photo_data(room: Room) -> tuple[str | None, list[str]]:
    folder_map = {
        "2-местный": "2_room",
        "2х местный номер": "2_room",
        "4-местный": "4_room",
        "4х местный номер": "4_room",
        "6-местный": "6_room",
        "6х местный номер": "6_room",
        "Домик на 2 места": "2_urta",
        "2х местный домик": "2_urta",
    }
    folder = folder_map.get(room.name)
    photo_dir = f"photos/{folder}" if folder else None
    photos = []
    if photo_dir and os.path.exists(photo_dir):
        photos = [
            os.path.join(photo_dir, file_name)
            for file_name in os.listdir(photo_dir)
            if file_name.lower().endswith((".jpg", ".png"))
        ]
        photos.sort()
    return folder, photos


def get_room_description_text(room: Room) -> str:
    price_override = (
        session.query(RoomPriceOverride)
        .filter(RoomPriceOverride.date >= date.today())
        .order_by(RoomPriceOverride.date.asc())
        .first()
    )
    price_per_place = price_override.price if price_override else PRICE_PER_ADULT
    display_name = room.name
    if room.name == "Домик на 2 места":
        display_name = room_type_names.get("5", room.name)
    elif room.name == "2-местный":
        display_name = room_type_names.get("2", room.name)
    elif room.name == "4-местный":
        display_name = room_type_names.get("4", room.name)
    elif room.name == "6-местный":
        display_name = room_type_names.get("6", room.name)
    price_text = f"{price_per_place}₽ за платное место/сутки"
    return (
        f"🏠 {display_name}\n"
        f"Вместимость: {room.capacity} чел.\n"
        f"Цена: {price_text}\n"
        f"Описание: {room.description}"
    )


def admin_create_room_keyboard(room_types: list[str]) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text=room_type_names.get(room_type, room_type))]
        for room_type in room_types
    ]
    keyboard.append([KeyboardButton(text=BTN_ADMIN), KeyboardButton(text=BTN_BACK)])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def admin_calendar_room_keyboard() -> ReplyKeyboardMarkup:
    room_items = list(room_type_names.items())
    rows = []
    for index in range(0, len(room_items), 2):
        rows.append(
            [
                KeyboardButton(text=room_items[index][1]),
                *(
                    [KeyboardButton(text=room_items[index + 1][1])]
                    if index + 1 < len(room_items)
                    else []
                ),
            ]
        )
    rows.append([KeyboardButton(text=BTN_ADMIN), KeyboardButton(text=BTN_MAIN_MENU)])
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def parse_guest_name_and_phone(raw_text: str) -> tuple[str | None, str | None]:
    phone_pattern = re.compile(r"(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)")
    for match in phone_pattern.finditer(raw_text):
        candidate = match.group(0).strip()
        digits = re.sub(r"\D", "", candidate)
        if 7 <= len(digits) <= 11:
            full_name = (raw_text[: match.start()] + raw_text[match.end() :]).strip(" ,;")
            full_name = re.sub(r"\s{2,}", " ", full_name)
            return (full_name or None), candidate
    return None, None


async def start_admin_create_booking(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        await message.answer("У вас нет доступа к админ-панели.")
        return
    await state.clear()
    await message.answer(
        "➕ Создание заявки администратором\n\nВведите количество человек:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text=BTN_ADMIN)], [KeyboardButton(text=BTN_BACK)]],
            resize_keyboard=True,
        ),
    )
    await state.set_state(BookingStates.waiting_for_admin_create_people)


@router.message(BookingStates.waiting_for_admin_create_people, F.text)
async def process_admin_create_people(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    if message.text in {BTN_ADMIN, BTN_BACK, BTN_MAIN_MENU}:
        await admin_panel(message, state)
        return
    try:
        people = int(message.text.strip())
    except ValueError:
        await message.answer("Введите число, например 16.")
        return
    if people <= 0:
        await message.answer("Количество человек должно быть больше 0.")
        return
    await state.update_data(admin_create_people=people)
    await message.answer("Введите количество суток:")
    await state.set_state(BookingStates.waiting_for_admin_create_duration)


@router.message(BookingStates.waiting_for_admin_create_duration, F.text)
async def process_admin_create_duration(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    if message.text in {BTN_ADMIN, BTN_BACK, BTN_MAIN_MENU}:
        await admin_panel(message, state)
        return
    try:
        duration = int(message.text.strip())
    except ValueError:
        await message.answer("Введите число суток, например 2.")
        return
    if duration <= 0 or duration > 31:
        await message.answer("Можно указать от 1 до 31 суток.")
        return

    data = await state.get_data()
    people = int(data["admin_create_people"])
    if people > 14:
        room_types = ["all"]
        note = (
            f"\n\n⚠️ Гостей больше 14: будет предложено «Все 14 мест», "
            f"а {people - 14} чел. пойдут плюсом в расчёт оплаты."
        )
    else:
        room_types = await get_optimal_room_combinations(people, duration=duration)
        note = ""
    if not room_types:
        room_types = ["all"]

    await state.update_data(
        admin_create_duration=duration,
        admin_create_room_types=room_types,
    )
    await message.answer(
        f"Выберите вариант проживания для {people} чел. на {get_duration_text(duration)}.{note}",
        reply_markup=admin_create_room_keyboard(room_types),
    )
    await state.set_state(BookingStates.waiting_for_admin_create_room)


@router.message(BookingStates.waiting_for_admin_create_room, F.text)
async def process_admin_create_room(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    if message.text in {BTN_ADMIN, BTN_BACK, BTN_MAIN_MENU}:
        await admin_panel(message, state)
        return
    data = await state.get_data()
    room_types = data.get("admin_create_room_types") or []
    room_type = next(
        (
            candidate
            for candidate in room_types
            if message.text.strip() == room_type_names.get(candidate, candidate)
        ),
        None,
    )
    if not room_type:
        await message.answer(
            "Выберите вариант кнопкой снизу.",
            reply_markup=admin_create_room_keyboard(room_types),
        )
        return
    duration = int(data["admin_create_duration"])
    await state.update_data(
        admin_create_room_type=room_type,
        room_type=room_type,
        duration=duration,
    )
    booking_year = session.query(BookingYear).first()
    year = booking_year.year if booking_year else date.today().year
    month = 6
    booked_dates = await get_booked_dates(room_type)
    calendar = await generate_calendar(
        year=year,
        month=month,
        booked_dates=booked_dates,
        min_date=date.today(),
        room_type=room_type,
        duration=duration,
        state=state,
    )
    await message.answer(
        f"📅 Выберите дату заезда для {room_type_names.get(room_type, room_type)}.\n"
        f"🟢 — свободно на {get_duration_text(duration)}\n"
        "🔴 — занято\n"
        "🔒 — дата в прошлом",
        reply_markup=calendar,
    )
    await state.set_state(BookingStates.waiting_for_admin_create_start_date)


@router.message(BookingStates.waiting_for_admin_create_start_date, F.text)
async def process_admin_create_start_date(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    if message.text in {BTN_ADMIN, BTN_BACK, BTN_MAIN_MENU}:
        await admin_panel(message, state)
        return
    data = await state.get_data()
    try:
        start_date = datetime.strptime(message.text.strip(), "%d.%m.%Y").date()
    except ValueError:
        await message.answer("Неверная дата. Пример: 15.07.2026")
        return
    duration = int(data["admin_create_duration"])
    end_date = start_date + timedelta(days=duration)
    room_type = data["admin_create_room_type"]
    occupied_dates, checkin_dates = await get_booked_dates(room_type)
    if not await is_room_available(
        room_type,
        start_date,
        end_date - timedelta(days=1),
        [d.isoformat() for d in occupied_dates],
        [d.isoformat() for d in checkin_dates],
    ):
        await message.answer("Этот период занят. Введите другую дату заезда.")
        return
    await state.update_data(
        admin_create_start_date=start_date.isoformat(),
        admin_create_end_date=end_date.isoformat(),
    )
    await message.answer(
        "Введите данные гостя одной строкой:\n"
        "ФИО и телефон\n\n"
        "Пример: Иванов Иван +79990000000",
    )
    await state.set_state(BookingStates.waiting_for_admin_create_guest)


@router.message(BookingStates.waiting_for_admin_create_guest, F.text)
async def process_admin_create_guest(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    if message.text in {BTN_ADMIN, BTN_BACK, BTN_MAIN_MENU}:
        await admin_panel(message, state)
        return
    raw_text = message.text.strip()
    full_name, phone = parse_guest_name_and_phone(raw_text)
    if not phone:
        await message.answer(
            "Не вижу номер телефона. Введите ФИО и номер одной строкой.\n"
            "Пример: Иванов Иван +79990000000"
        )
        return
    if not full_name:
        await message.answer("Не вижу ФИО. Пример: Иванов Иван +79990000000")
        return
    guest_note = ""
    data = await state.get_data()
    start_date = datetime.fromisoformat(data["admin_create_start_date"]).date()
    end_date = datetime.fromisoformat(data["admin_create_end_date"]).date()
    people = int(data["admin_create_people"])
    total = await calculate_revenue(
        data["admin_create_room_type"],
        people,
        start_date,
        end_date,
    )
    await state.update_data(
        admin_create_full_name=full_name,
        admin_create_phone=phone,
        admin_create_guest_note=guest_note,
        admin_create_calculated_total=total,
    )
    await message.answer(
        f"Расчётная сумма: {total}₽\n\n"
        "Введите оплату одной строкой:\n"
        "способ оплаты и внесённая сумма\n\n"
        "Пример: перевод 10000\n"
        "Пример: наличные 5000",
    )
    await state.set_state(BookingStates.waiting_for_admin_create_payment)


@router.message(BookingStates.waiting_for_admin_create_payment, F.text)
async def process_admin_create_payment(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    if message.text in {BTN_ADMIN, BTN_BACK, BTN_MAIN_MENU}:
        await admin_panel(message, state)
        return
    data = await state.get_data()
    raw_text = message.text.strip()
    amount_match = re.search(r"\d[\d\s]*", raw_text)
    if not amount_match:
        await message.answer("Не вижу сумму. Пример: перевод 10000")
        return
    paid = int(re.sub(r"\D", "", amount_match.group(0)))
    payment_method = (raw_text[: amount_match.start()] + raw_text[amount_match.end() :]).strip() or "не указан"
    total = int(data["admin_create_calculated_total"])

    start_date = datetime.fromisoformat(data["admin_create_start_date"]).date()
    end_date = datetime.fromisoformat(data["admin_create_end_date"]).date()
    people = int(data["admin_create_people"])
    room_type = data["admin_create_room_type"]
    remaining = max(total - paid, 0)
    status = BookingStatus.PAID.value if remaining == 0 else BookingStatus.AWAITING_PAYMENT.value
    booking = Booking(
        user_id=ADMIN_CHAT_ID,
        username="admin_manual",
        full_name=data["admin_create_full_name"],
        phone=data["admin_create_phone"],
        comment=data.get("admin_create_guest_note", ""),
        date_from=start_date,
        date_to=end_date,
        room_type=room_type,
        adults=people,
        children=0,
        children_beds="[]",
        status=status,
        manual_total=total,
        paid_amount=paid,
        payment_method=payment_method,
        admin_comment=(
            f"Создано администратором. Оплата: {payment_method}. "
            f"Внесено: {paid}₽. Осталось: {remaining}₽."
        ),
    )
    session.add(booking)
    session.commit()
    clear_booked_dates_cache()
    session.add(
        AdminLog(
            admin_id=message.from_user.id,
            booking_id=booking.id,
            action=f"Создал ручную заявку #{booking.id}",
        )
    )
    session.commit()
    await state.clear()
    await message.answer(
        f"✅ Заявка #{booking.id} создана\n\n"
        f"👤 {booking.full_name}\n"
        f"📞 {booking.phone}\n"
        f"🏠 {room_type_names.get(room_type, room_type)}\n"
        f"📅 {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}\n"
        f"👥 Гостей: {people}\n"
        f"💰 Итого: {total}₽\n"
        f"✅ Внесли: {paid}₽\n"
        f"🧾 Осталось: {remaining}₽\n"
        f"📍 Статус: {BOOKING_STATUS_LABELS.get(status, status)}",
        reply_markup=admin_menu_keyboard(),
    )


async def start_admin_payment_update(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    await state.clear()
    await message.answer(
        "💳 Изменение внесённой суммы\n\n"
        "Введите ID заявки и новую внесённую сумму.\n"
        "Пример: 12 15000",
        reply_markup=admin_menu_keyboard(),
    )
    await state.set_state(BookingStates.waiting_for_admin_payment_update)


@router.callback_query(F.data.startswith("admin_update_payment_"))
async def admin_update_payment_from_card(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав", show_alert=True)
        return
    booking_id = int(callback.data.replace("admin_update_payment_", "", 1))
    booking = session.query(Booking).filter_by(id=booking_id).first()
    if not booking:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    await state.update_data(admin_payment_booking_id=booking_id)
    await callback.message.answer(
        f"💳 Заявка #{booking_id}\n"
        f"Сейчас внесено: {booking.paid_amount or 0}₽\n\n"
        "Введите новую общую внесённую сумму.\n"
        "Пример: 15000",
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder=f"Сумма оплаты заявки #{booking_id}",
        ),
    )
    await state.set_state(BookingStates.waiting_for_admin_payment_update)
    await callback.answer()


@router.message(BookingStates.waiting_for_admin_payment_update, F.text)
async def process_admin_payment_update(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    if message.text in {BTN_ADMIN, BTN_BACK, BTN_MAIN_MENU}:
        await admin_panel(message, state)
        return
    data = await state.get_data()
    numbers = re.findall(r"\d+", message.text)
    if data.get("admin_payment_booking_id"):
        if not numbers:
            await message.answer("Введите новую внесённую сумму. Пример: 15000")
            return
        booking_id = int(data["admin_payment_booking_id"])
        paid = int(numbers[0])
    else:
        if len(numbers) < 2:
            await message.answer("Введите ID заявки и сумму. Пример: 12 15000")
            return
        booking_id = int(numbers[0])
        paid = int(numbers[1])
    booking = session.query(Booking).filter_by(id=booking_id).first()
    if not booking:
        await message.answer(f"Заявка #{booking_id} не найдена.")
        return
    children_beds = get_children_beds(booking)
    total_people = booking.adults + sum(children_beds)
    calculated_total = await calculate_revenue(
        booking.room_type,
        total_people,
        booking.date_from,
        booking.date_to,
    )
    balance = apply_confirmed_payment(booking, paid, calculated_total)
    total = balance["total"]
    remaining = balance["remaining"]
    booking.admin_comment = (
        f"{booking.admin_comment or ''}\n"
        f"Оплата обновлена администратором. Внесено: {paid}₽. Осталось: {remaining}₽."
    ).strip()
    session.add(
        AdminLog(
            admin_id=message.from_user.id,
            booking_id=booking.id,
            action=f"Изменил внесённую сумму заявки #{booking.id} на {paid}₽",
        )
    )
    session.commit()
    session.expire_all()
    await state.clear()
    if booking.user_id:
        await message.bot.send_message(
            booking.user_id,
            (
                f"💳 Оплата по заявке #{booking.id} обновлена администратором.\n\n"
                f"💰 Общая сумма: {total}₽\n"
                f"✅ Внесено: {paid}₽\n"
                f"🧾 Осталось оплатить: {remaining}₽"
            ),
        )
    await message.answer(
        f"✅ Оплата заявки #{booking.id} обновлена\n\n"
        f"💰 Итого: {total}₽\n"
        f"✅ Внесено: {paid}₽\n"
        f"🧾 Осталось: {remaining}₽\n"
        f"📍 Статус: {payment_status_label(balance) or BOOKING_STATUS_LABELS.get(booking.status, booking.status)}",
        reply_markup=admin_menu_keyboard(),
    )


def get_room_gallery_keyboard(
    include_room_switch: bool,
    back_callback: str = "about_us",
    back_text: str = "⬅️ К разделу О нас",
) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="prev_photo"),
            InlineKeyboardButton(text="➡️ Вперед", callback_data="next_photo"),
        ]
    ]
    if include_room_switch:
        keyboard.append(
            [
                InlineKeyboardButton(text="⬅️ Пред. номер", callback_data="prev_room"),
                InlineKeyboardButton(text="След. номер ➡️", callback_data="next_room"),
            ]
        )
    if include_room_switch:
        back_callback = "rooms_description"
        back_text = "⬅️ К списку номеров"
    keyboard.append([InlineKeyboardButton(text=back_text, callback_data=back_callback)])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


async def render_room_gallery(
    callback: CallbackQuery,
    state: FSMContext,
    bot: Bot,
    room: Room,
    room_index: int,
    rooms: list[str],
):
    folder, photos = get_room_photo_data(room)
    text = get_room_description_text(room)
    include_room_switch = bool(rooms)

    await state.set_state(BookingStates.viewing_photos)
    await state.update_data(
        photo_folder=folder,
        photo_index=0,
        photo_list=photos,
        current_room=room.name,
        room_index=room_index,
        rooms=rooms,
    )

    if not photos:
        message_text = f"{text}\n\n⚠️ Фотографии номера недоступны."
        try:
            await callback.message.edit_text(
                message_text, reply_markup=None
            )
        except TelegramBadRequest:
            await callback.message.delete()
            await callback.message.answer(
                message_text,
                reply_markup=get_room_gallery_keyboard(include_room_switch),
            )
        return

    file_id = await get_file_id_with_fallback(bot, photos[0], callback.from_user.id)
    caption = f"{text}\n\nФото 1/{len(photos)}"
    media = InputMediaPhoto(media=file_id, caption=caption)
    try:
        await callback.message.edit_media(
            media=media,
            reply_markup=get_room_gallery_keyboard(include_room_switch),
        )
    except (TelegramBadRequest, AttributeError):
        await callback.message.delete()
        await callback.message.answer_photo(
            photo=file_id,
            caption=caption,
            reply_markup=get_room_gallery_keyboard(include_room_switch),
        )


@router.callback_query(
    F.data.in_(["next_room", "prev_room"]), StateFilter(BookingStates.viewing_photos)
)
async def navigate_rooms(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    room_names = data.get("rooms") or [
        room.name for room in session.query(Room).filter(Room.is_available == True).all()
    ]
    if not room_names:
        await callback.answer("Нет доступных комнат.", show_alert=True)
        return

    current_index = data.get("room_index")
    if current_index is None:
        current_room = data.get("current_room")
        current_index = room_names.index(current_room) if current_room in room_names else 0

    step = 1 if callback.data == "next_room" else -1
    room_index = (current_index + step) % len(room_names)
    room = session.query(Room).filter_by(name=room_names[room_index]).first()
    if not room:
        await callback.answer("Комната не найдена.", show_alert=True)
        return

    await render_room_gallery(callback, state, bot, room, room_index, room_names)
    logging.info(
        f"Пользователь {callback.from_user.id} переключил обзор комнат на {room.name}"
    )
    await callback.answer()


@router.callback_query(
    F.data.in_(["next_photo", "prev_photo"]), StateFilter(BookingStates.viewing_photos)
)
async def navigate_photos(callback: CallbackQuery, state: FSMContext, bot: Bot):
    data = await state.get_data()
    photo_folder = data.get("photo_folder")
    photo_index = data.get("photo_index", 0)
    photo_list = data.get("photo_list", [])
    current_room = data.get("current_room")
    photo_descriptions = data.get("photo_descriptions", {})
    general_text = data.get("general_text", "")

    if not photo_list:
        try:
            await callback.message.edit_caption(
                caption="⚠️ Фотографии недоступны.", reply_markup=None
            )
        except TelegramBadRequest as e:
            if "message to edit not found" in str(
                e
            ) or "there is no caption in the message to edit" in str(e):
                await callback.message.delete()
                await callback.message.answer(
                    "⚠️ Фотографии недоступны.", reply_markup=about_menu_keyboard()
                )
            else:
                logging.error(
                    f"Ошибка при редактировании подписи в navigate_photos для пользователя {callback.from_user.id}: {str(e)}\n{traceback.format_exc()}"
                )
                raise
        await state.clear()
        logging.error(
            f"Ошибка в navigate_photos для пользователя {callback.from_user.id}: нет списка фотографий"
        )
        await callback.answer()
        return

    # Сохраняем текущий индекс для сравнения
    current_photo_index = photo_index
    photo_index = (
        (photo_index + 1) % len(photo_list)
        if callback.data == "next_photo"
        else (photo_index - 1) % len(photo_list)
    )

    # Если индекс не изменился, пропускаем обновление
    if current_photo_index == photo_index:
        logging.debug(
            f"Пользователь {callback.from_user.id} вернулся к той же фотографии (индекс {photo_index}), обновление не требуется"
        )
        await callback.answer()
        return

    await state.update_data(photo_index=photo_index)

    try:
        if photo_folder == "territory":
            photo_name = (
                os.path.basename(photo_list[photo_index]).lower().replace(" ", "")
            )
            logging.info(f"Переключение фото территории: {photo_name}")
            file_id = await get_file_id_with_fallback(bot, photo_list[photo_index], callback.from_user.id)
            description = photo_descriptions.get(photo_name, "")
            caption = f"{general_text}\n{description}\n\nФото {photo_index + 1}/{len(photo_list)}"

            # Проверяем, изменилось ли содержимое
            current_caption = getattr(callback.message, "caption", "") or ""
            current_photo = getattr(callback.message, "photo", None)
            current_media = current_photo[-1].file_id if current_photo else None
            if current_media == file_id and current_caption == caption:
                logging.debug(
                    f"Содержимое сообщения не изменилось для territory, file_id={file_id}, caption={caption}"
                )
                await callback.answer()
                return

            media = InputMediaPhoto(media=file_id, caption=caption)
            try:
                await callback.message.edit_media(
                    media=media,
                    reply_markup=get_room_gallery_keyboard(False),
                )
            except (TelegramBadRequest, AttributeError) as e:
                if "message to edit not found" in str(e):
                    try:
                        await callback.message.delete()
                    except TelegramBadRequest:
                        pass
                    await callback.message.answer_photo(
                        photo=file_id,
                        caption=caption,
                        reply_markup=get_room_gallery_keyboard(False),
                    )
                elif "message is not modified" in str(e):
                    logging.debug(
                        f"Сообщение не изменилось для territory, file_id={file_id}, caption={caption}"
                    )
                    await callback.answer()
                else:
                    logging.error(
                        f"Ошибка при редактировании медиа в navigate_photos для territory, пользователь {callback.from_user.id}: {str(e)}\n{traceback.format_exc()}"
                    )
                    raise
            logging.info(
                f"Пользователь {callback.from_user.id} переключил фото в territory на индекс {photo_index}, описание: {description}"
            )
        else:
            room = session.query(Room).filter_by(name=current_room).first()
            text = get_room_description_text(room)
            photo_name = os.path.basename(photo_list[photo_index]).lower()
            logging.info(f"Переключение фото комнаты {room.name}: {photo_name}")
            file_id = await get_file_id_with_fallback(bot, photo_list[photo_index], callback.from_user.id)
            caption = f"{text}\n\nФото {photo_index + 1}/{len(photo_list)}"
            # Проверяем, изменилось ли содержимое
            current_caption = getattr(callback.message, "caption", "") or ""
            current_photo = getattr(callback.message, "photo", None)
            current_media = current_photo[-1].file_id if current_photo else None
            if current_media == file_id and current_caption == caption:
                logging.debug(
                    f"Содержимое сообщения не изменилось для комнаты {room.name}, file_id={file_id}, caption={caption}"
                )
                await callback.answer()
                return

            media = InputMediaPhoto(media=file_id, caption=caption)
            try:
                await callback.message.edit_media(
                    media=media,
                    reply_markup=get_room_gallery_keyboard(bool(data.get("rooms"))),
                )
            except (TelegramBadRequest, AttributeError) as e:
                if "message to edit not found" in str(e):
                    try:
                        await callback.message.delete()
                    except TelegramBadRequest:
                        pass
                    await callback.message.answer_photo(
                        photo=file_id,
                        caption=caption,
                        reply_markup=get_room_gallery_keyboard(bool(data.get("rooms"))),
                    )
                elif "message is not modified" in str(e):
                    logging.debug(
                        f"Сообщение не изменилось для комнаты {room.name}, file_id={file_id}, caption={caption}"
                    )
                    await callback.answer()
                else:
                    logging.error(
                        f"Ошибка при редактировании медиа в navigate_photos для комнаты {room.name}, пользователь {callback.from_user.id}: {str(e)}\n{traceback.format_exc()}"
                    )
                    raise
            logging.info(
                f"Пользователь {callback.from_user.id} переключил фото в {current_room} на индекс {photo_index}"
            )
    except Exception as e:
        try:
            await callback.message.edit_caption(
                caption=f"{caption}\n\n⚠️ Ошибка при загрузке: {str(e)}",
                reply_markup=None,
            )
        except TelegramBadRequest as e_inner:
            if (
                "message to edit not found" in str(e_inner)
                or "message caption is not modified" in str(e_inner)
                or "there is no caption in the message to edit" in str(e_inner)
            ):
                try:
                    await callback.message.delete()
                except TelegramBadRequest:
                    pass
                await callback.message.answer(
                    f"{caption}\n\n⚠️ Ошибка при загрузке: {str(e)}",
                    reply_markup=get_room_gallery_keyboard(photo_folder != "territory"),
                )
            else:
                logging.error(
                    f"Ошибка при редактировании подписи в navigate_photos для пользователя {callback.from_user.id}: {str(e_inner)}\n{traceback.format_exc()}"
                )
                raise
        logging.error(
            f"Ошибка в navigate_photos для {photo_folder or current_room}, пользователь {callback.from_user.id}: {str(e)}\n{traceback.format_exc()}"
        )
    await callback.answer()


# Глобальный словарь для хранения локов по пользователям
user_locks = {}


async def get_user_lock(user_id: int) -> Lock:
    if user_id not in user_locks:
        user_locks[user_id] = Lock()
    return user_locks[user_id]


async def save_current_booking_draft(user_id: int, state: FSMContext) -> None:
    data = await state.get_data()
    if data:
        save_booking_draft(user_id, data)


async def ask_booking_duration(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.edit_text("📅 На сколько суток вы хотите заехать?")
    await state.set_state(BookingStates.waiting_for_duration)


async def show_booking_resume_prompt(callback: CallbackQuery, draft: dict) -> None:
    details = []
    if draft.get("duration"):
        details.append(f"суток: {draft['duration']}")
    if draft.get("adults"):
        details.append(f"взрослых: {draft['adults']}")
    if draft.get("children") is not None:
        details.append(f"детей: {draft['children']}")
    if draft.get("room_type"):
        details.append(f"номер: {room_type_names.get(draft['room_type'], draft['room_type'])}")
    summary = ", ".join(details) if details else "данные прошлого шага сохранены"
    await callback.message.edit_text(
        f"У вас есть незавершенное бронирование: {summary}.\nПродолжить с этого места?",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="Продолжить", callback_data="resume_booking_draft")],
                [InlineKeyboardButton(text="Начать заново", callback_data="new_booking_ignore_draft")],
                [InlineKeyboardButton(text="⬅️ В меню", callback_data="back_to_main")],
            ]
        ),
    )



@router.callback_query(
    F.data.startswith("month_"), StateFilter(BookingStates.viewing_availability_calendar)
)
async def paginate_availability_calendar(callback: CallbackQuery, state: FSMContext):
    try:
        _, year, month, room_type = callback.data.split("_", 3)
        year, month = int(year), int(month)
        current_year = get_booking_year_value()
        if year != current_year or month not in [6, 7, 8]:
            await callback.answer(
                f"Выберите месяц сезона {current_year} года.",
                show_alert=True,
            )
            return

        data = await state.get_data()
        people = data.get("availability_people", 1)
        duration = data.get("availability_duration", data.get("duration", 1))
        booked_dates = await get_booked_dates(room_type)
        calendar = await generate_calendar(
            year=year,
            month=month,
            booked_dates=booked_dates,
            min_date=date.today(),
            room_type=room_type,
            duration=duration,
            state=state,
        )
        add_availability_calendar_back_buttons(calendar)
        await callback.message.edit_text(
            get_availability_calendar_text(room_type, year, people, duration),
            reply_markup=calendar,
        )
        await callback.answer()
    except Exception as e:
        logging.error(
            f"Ошибка в paginate_availability_calendar: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer("Не удалось переключить месяц. Попробуйте снова.", show_alert=True)


@router.callback_query(
    F.data.startswith("month_"), StateFilter(BookingStates.waiting_for_start_date)
)
async def paginate_calendar_start(callback: CallbackQuery, state: FSMContext):
    try:
        _, year, month, room_type = callback.data.split("_")
        year, month = int(year), int(month)
        with session as db_session:
            booking_year = db_session.query(BookingYear).first()
            current_year = booking_year.year if booking_year else 2025
        ALLOWED_MONTHS = [6, 7, 8]
        # Для гостей разрешаем только текущий год
        if year != current_year or month not in ALLOWED_MONTHS:
            await callback.answer(
                f"Бронирование доступно только на июнь, июль, август в {current_year} году.",
                show_alert=True,
            )
            return
        data = await state.get_data()
        duration = data.get("duration", 1)
        booked_dates = await get_booked_dates(room_type)
        await callback.bot.send_chat_action(
            chat_id=callback.message.chat.id, action="typing"
        )
        calendar = await generate_calendar(
            year=year,
            month=month,
            booked_dates=booked_dates,
            room_type=room_type,
            duration=duration,
            state=state,
        )
        await callback.message.edit_text(
            f"📅 Выберите дату заезда для номера {room_type_names[room_type]} в {year} году:\n"
            f"🟢 — свободные даты для заезда\n"
            f"🔴 — занятые даты\n"
            f"🔒 — недоступные даты",
            reply_markup=calendar,
        )
        logging.info(
            f"Пользователь {callback.from_user.id} переключил календарь на {year}-{month} для {room_type}"
        )
    except Exception as e:
        logging.error(
            f"Ошибка в paginate_calendar_start для year {year}, month {month}: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.message.answer(
            "⚠️ Ошибка при загрузке календаря. Попробуйте снова или свяжитесь с поддержкой."
        )
        await state.clear()
    await callback.answer()


@router.callback_query(F.data == "no_dates")
async def no_dates_available(callback: CallbackQuery, state: FSMContext):
    await callback.message.answer(
        "К сожалению, в этом месяце нет доступных дат для бронирования. Попробуйте другой месяц или свяжитесь с поддержкой."
    )
    await state.clear()
    await cmd_start(callback.message)



@router.callback_query(
    F.data.regexp(r"day_(\d+)_(\d+)_(\d+)"),
    StateFilter(BookingStates.viewing_availability_calendar),
)
async def select_availability_date(callback: CallbackQuery, state: FSMContext):
    try:
        _, year, month, day = callback.data.split("_")
        selected_date = date(int(year), int(month), int(day))
        data = await state.get_data()
        room_type = data.get("room_type")
        people = data.get("availability_people", 1)
        duration = data.get("availability_duration", data.get("duration", 1))
        if not await is_available_for_duration(room_type, selected_date, duration):
            await callback.answer(
                "На выбранный период номер уже занят. Выберите другую дату.",
                show_alert=True,
            )
            return
        end_date = selected_date + timedelta(days=duration)
        await state.update_data(
            availability_selected_date=selected_date.strftime("%Y-%m-%d"),
            start_date=selected_date.strftime("%Y-%m-%d"),
            end_date=end_date.strftime("%Y-%m-%d"),
            duration=duration,
            adults=people,
            children=0,
            children_beds=[],
            room_type=room_type,
        )
        await callback.message.edit_text(
            f"✅ Дата свободна: {selected_date.strftime('%d.%m.%Y')}\n"
            f"Выезд: {end_date.strftime('%d.%m.%Y')} до 12:00\n"
            f"Номер: {room_type_names.get(room_type, room_type)}\n"
            f"Гостей: {people}\n"
            f"Суток: {duration}\n\n"
            f"Можно перейти к бронированию или вернуться к календарю.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="🏡 Забронировать", callback_data="availability_book")],
                    [InlineKeyboardButton(text="⬅️ Назад к календарю", callback_data=f"availability_room_{room_type}")],
                    [InlineKeyboardButton(text="🏠 Главное меню", callback_data="back_to_main")],
                ]
            ),
        )
        await callback.answer()
        logging.info(
            "User %s selected available date %s for %s, people=%s duration=%s",
            callback.from_user.id,
            selected_date,
            room_type,
            people,
            duration,
        )
    except Exception as e:
        logging.error(
            f"Ошибка в select_availability_date: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer("Не удалось выбрать дату. Попробуйте снова.", show_alert=True)


@router.callback_query(F.data == "availability_book")
async def start_booking_from_availability(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    required = ["room_type", "start_date", "end_date", "duration", "adults"]
    if any(key not in data for key in required):
        await callback.answer("Данные проверки устарели. Начните проверку заново.", show_alert=True)
        return
    start_date = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
    end_date = datetime.strptime(data["end_date"], "%Y-%m-%d").date()
    total_people = int(data["adults"])
    total = await calculate_revenue(data["room_type"], total_people, start_date, end_date)
    person_day_price_text = await get_person_day_price_text(
        data["room_type"], total_people, start_date, end_date
    )
    await state.update_data(
        children=0,
        children_beds=[],
        total_price=total,
        user_id=callback.from_user.id,
    )
    await save_current_booking_draft(callback.from_user.id, state)
    confirmation_text = (
        "<b>Подтверждение бронирования:</b>\n"
        f"Количество гостей: {total_people}\n"
        f"🏠 Номер: {room_type_names[data['room_type']]}\n"
        f"📅 Заезд: {start_date.strftime('%d.%m.%Y')} после 14:00\n"
        f"📅 Выезд: {end_date.strftime('%d.%m.%Y')} до 12:00 ({get_duration_text(data['duration'])})\n"
        f"{person_day_price_text}"
        "Мы передадим заявку администратору для подтверждения.\n"
        f"💰 Сумма: {total}₽"
    )
    await callback.message.edit_text(
        confirmation_text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="✅ Подтвердить", callback_data="confirm_booking")],
                [InlineKeyboardButton(text="❌ Отменить", callback_data="cancel_booking")],
            ]
        ),
    )
    await state.set_state(BookingStates.confirm_booking)
    await callback.answer()



@router.callback_query(F.data.regexp(r"day_(\d+)_(\d+)_(\d+)"))
async def select_date(callback: CallbackQuery, state: FSMContext):
    try:
        current_state = await state.get_state()
        logging.debug(
            f"Обработка callback: data={callback.data}, state={current_state}"
        )
        parts = callback.data.split("_")
        if len(parts) != 4:
            logging.error(
                f"Некорректный формат callback-данных: {callback.data}, ожидается day_<year>_<month>_<day>"
            )
            await callback.answer("Ошибка в формате даты.", show_alert=True)
            return

        _, year, month, day = parts
        year, month, day = int(year), int(month), int(day)
        with session as db_session:
            booking_year = db_session.query(BookingYear).first()
            current_booking_year = booking_year.year if booking_year else 2025
        ALLOWED_MONTHS = [6, 7, 8]
        if year != current_booking_year or month not in ALLOWED_MONTHS:
            await callback.answer(
                f"Бронирование доступно только на июнь, июль, август в {current_booking_year} году.",
                show_alert=True,
            )
            return

        selected_date = datetime(year, month, day).date()
        today = date.today()
        if selected_date < today:
            await callback.answer("Нельзя выбрать дату в прошлом.", show_alert=True)
            return

        data = await state.get_data()
        if current_state == BookingStates.waiting_for_admin_create_start_date:
            room_type = data.get("admin_create_room_type") or data.get("room_type")
            duration = int(data.get("admin_create_duration", data.get("duration", 1)))
            end_date = selected_date + timedelta(days=duration)
            occupied_dates, checkin_dates = await get_booked_dates(room_type)
            if not await is_room_available(
                room_type,
                selected_date,
                end_date - timedelta(days=1),
                [d.isoformat() for d in occupied_dates],
                [d.isoformat() for d in checkin_dates],
            ):
                await callback.answer("Этот период занят. Выберите зелёную дату.", show_alert=True)
                return
            await state.update_data(
                admin_create_start_date=selected_date.isoformat(),
                admin_create_end_date=end_date.isoformat(),
            )
            await callback.message.edit_text(
                f"✅ Дата выбрана\n\n"
                f"🏠 {room_type_names.get(room_type, room_type)}\n"
                f"📅 Заезд: {selected_date.strftime('%d.%m.%Y')}\n"
                f"📅 Выезд: {end_date.strftime('%d.%m.%Y')} до 12:00\n"
                f"🌙 {get_duration_text(duration)}\n\n"
                "Введите данные гостя одной строкой:\n"
                "ФИО; телефон; username или комментарий\n\n"
                "Пример: Иванов Иван; +79990000000; @ivan"
            )
            await state.set_state(BookingStates.waiting_for_admin_create_guest)
            await callback.answer()
            return

        # Проверка: в админ-режиме выдаём сообщение "Выберите занятую дату"
        is_admin_mode = data.get("is_admin_mode", False)
        if is_admin_mode:
            if current_state in {
                BookingStates.waiting_for_price_change_start_date,
                BookingStates.waiting_for_price_change_end_date,
            }:
                await state.update_data(is_admin_mode=False)
            else:
                await callback.answer("Выберите занятую дату.", show_alert=True)
                return

        # Проверяем наличие необходимых данных для бронирования
        room_type = data.get("room_type")
        if not room_type:
            logging.warning(
                f"room_type missing in state for user {callback.from_user.id}; restarting booking flow"
            )
            await callback.answer(
                "Данные бронирования устарели. Начните выбор даты заново.",
                show_alert=True,
            )
            await state.clear()
            await callback.message.answer("На сколько суток планируете заезд? Например, 2:")
            await state.set_state(BookingStates.waiting_for_duration)
            return

        duration = data.get("duration", 1)
        adults = data.get("adults", 1)
        children = data.get("children", 0)
        children_beds = data.get("children_beds", [])
        children_needing_beds = sum(children_beds)
        total_people = adults + children_needing_beds

        if current_state == BookingStates.waiting_for_start_date:
            start_date = selected_date
            end_date = start_date + timedelta(days=duration)
            occupied_dates, checkin_dates = await get_booked_dates(room_type)
            logging.debug(
                f"Проверка доступности для {room_type}: start_date={start_date}, end_date={end_date}, occupied={[d.isoformat() for d in occupied_dates]}, checkin={[d.isoformat() for d in checkin_dates]}"
            )
            # Проверяем период до (start_date + duration - 1)
            check_end_date = start_date + timedelta(days=duration - 1)
            if not await is_room_available(
                room_type,
                start_date,
                check_end_date,
                [d.isoformat() for d in occupied_dates],
                [d.isoformat() for d in checkin_dates],
            ):
                await callback.answer(
                    "Этот период недоступен для бронирования.", show_alert=True
                )
                logging.info(
                    f"Период {start_date}–{check_end_date} недоступен для номера {room_type}"
                )
                return

            # Дополнительная проверка перед сохранением
            occupied_dates, checkin_dates = await get_booked_dates(
                room_type
            )  # Обновляем данные
            if not await is_room_available(
                room_type,
                start_date,
                check_end_date,
                [d.isoformat() for d in occupied_dates],
                [d.isoformat() for d in checkin_dates],
            ):
                await callback.answer(
                    "Период стал недоступен. Попробуйте выбрать другие даты.",
                    show_alert=True,
                )
                logging.info(
                    f"Период {start_date}–{check_end_date} стал недоступен для номера {room_type} после повторной проверки"
                )
                return

            await state.update_data(start_date=start_date.strftime("%Y-%m-%d"))
            await save_current_booking_draft(callback.from_user.id, state)
            min_end_date = end_date
            max_end_date = min_end_date

            booked_dates = await get_booked_dates(room_type)
            logging.debug(
                f"Передача booked_dates для календаря выезда: occupied={[d.isoformat() for d in booked_dates[0]]}, checkin={[d.isoformat() for d in booked_dates[1]]}"
            )

            # Определяем месяц и год для даты выезда
            end_year = end_date.year
            end_month = end_date.month

            calendar = await generate_calendar(
                year=end_year,  # Используем год даты выезда
                month=end_month,  # Используем месяц даты выезда
                booked_dates=booked_dates,
                min_date=min_end_date,
                max_date=max_end_date,
                room_type=room_type,
                is_checkout_calendar=True,
                selected_start_date=start_date,
                duration=duration,
            )
            await callback.message.edit_text(
                f"📅 Вы выбрали проживание на {get_duration_text(duration)}. Доступная дата выезда: {min_end_date.strftime('%d.%m.%Y')} до 12:00.\n"
                f"🟢 — доступная дата выезда (на основе {get_duration_text(duration)})\n"
                f"🔴 — недоступные даты",
                reply_markup=calendar,
            )
            await state.set_state(BookingStates.choosing_month_for_end_date)
            logging.info(
                f"Пользователь {callback.from_user.id} выбрал дату заезда {start_date} для {room_type}, перешёл к выбору даты выезда на {end_year}-{end_month}"
            )
            await callback.answer()

        elif current_state == BookingStates.choosing_month_for_end_date:
            end_date = selected_date
            if "start_date" not in data:
                await callback.answer(
                    "Ошибка: дата заезда не выбрана. Начните бронирование заново.",
                    show_alert=True,
                )
                await state.clear()
                await callback.message.answer(
                    "📅 Введите количество суток (например, 2):"
                )
                await state.set_state(BookingStates.waiting_for_duration)
                return
            start_date = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
            expected_end_date = start_date + timedelta(days=duration)

            logging.info(
                f"Пользователь {callback.from_user.id} выбирает дату выезда {end_date} для номера {room_type}, {total_people} человек"
            )

            if end_date != expected_end_date:
                await callback.answer(
                    f"Дата выезда должна быть {expected_end_date.strftime('%d.%m.%Y')} до 12:00, так как вы выбрали проживание на {get_duration_text(duration)}.",
                    show_alert=True,
                )
                return

            occupied_dates, checkin_dates = await get_booked_dates(room_type)
            logging.debug(
                f"Проверка доступности для {room_type}: start_date={start_date}, end_date={end_date}, occupied={[d.isoformat() for d in occupied_dates]}, checkin={[d.isoformat() for d in checkin_dates]}"
            )
            # Проверяем период до (end_date - 1)
            check_end_date = end_date - timedelta(days=1)
            if not await is_room_available(
                room_type,
                start_date,
                check_end_date,
                [d.isoformat() for d in occupied_dates],
                [d.isoformat() for d in checkin_dates],
            ):
                await callback.answer("Выбранный период недоступен.", show_alert=True)
                logging.info(
                    f"Период {start_date}–{check_end_date} недоступен для номера {room_type}"
                )
                return

            await state.update_data(end_date=end_date.strftime("%Y-%m-%d"))
            total = await calculate_revenue(
                room_type, total_people, start_date, end_date
            )
            person_day_price_text = await get_person_day_price_text(
                room_type, total_people, start_date, end_date
            )
            await state.update_data(total_price=total)
            await save_current_booking_draft(callback.from_user.id, state)

            # Формируем текст подтверждения с учётом логики цен
            price_per_day_text = ""
            if room_type == "all" and 8 <= total_people <= 10:
                price_per_day_text = (
                    f"💰 Цена за сутки: {ALL_ROOMS_PRICE}₽ (фиксированная)\n"
                )
            else:
                price_per_day_text = (
                    "💰 Стоимость рассчитана по выбранным датам и количеству платных мест.\n"
                )

            confirmation_text = (
                "<b>Подтвердите бронирование:</b>\n"
                f"👨‍👩‍👧‍👦 Всего человек: {total_people} (взрослых: {adults}, детей: {children}, из них {children_needing_beds} с местами)\n"
                f"🏠 Номер: {room_type_names[room_type]}\n"
                f"📅 Заезд: {start_date.strftime('%d.%m.%Y')} после 14:00\n"
                f"📅 Выезд: {end_date.strftime('%d.%m.%Y')} до 12:00 ({get_duration_text(duration)})\n"
                f"{person_day_price_text}"
                f"{price_per_day_text}"
                f"💰 Сумма: {total}₽"
            )
            logging.debug(f"Сформировано сообщение подтверждения:\n{confirmation_text}")

            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Подтвердить", callback_data="confirm_booking"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="❌ Отменить", callback_data="cancel_booking"
                        )
                    ],
                ]
            )
            logging.debug(
                f"Создана клавиатура подтверждения бронирования: callback_data=confirm_booking, cancel_booking, user={callback.from_user.id}, message_id={callback.message.message_id}"
            )
            await callback.message.edit_text(
                confirmation_text, parse_mode="HTML", reply_markup=kb
            )
            await state.set_state(BookingStates.confirm_booking)
            logging.info(
                f"Пользователь {callback.from_user.id} подтвердил бронирование для {room_type}, {start_date}–{end_date}, сумма={total}₽"
            )
            await callback.answer()

    except Exception as e:
        logging.error(
            f"Ошибка в select_date для пользователя {callback.from_user.id}: {str(e)}\n{traceback.format_exc()}"
        )
        error_message = "Произошла ошибка. Попробуйте снова."
        try:
            await callback.answer(error_message, show_alert=True)
        except Exception as answer_error:
            logging.error(
                f"Не удалось отправить callback.answer: {str(answer_error)}\n{traceback.format_exc()}"
            )
        await state.clear()
        await callback.message.answer("📅 Введите количество суток (например, 2):")
        await state.set_state(BookingStates.waiting_for_duration)


@router.callback_query(F.data == "confirm_booking")
async def ask_contact(callback: CallbackQuery, state: FSMContext):
    try:
        current_state = await state.get_state()
        logging.info(
            f"Вызов ask_contact для пользователя {callback.from_user.id}, callback_data={callback.data}, состояние={current_state}"
        )
        # Убираем кнопки из сообщения подтверждения
        await callback.message.edit_reply_markup(reply_markup=None)
        contact_kb = ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Отправить контакт", request_contact=True)],
                [KeyboardButton(text="✏️ Ввести вручную")],
            ],
            resize_keyboard=True,
        )
        await callback.message.answer(
            "Отправьте ваш контакт или введите данные вручную", reply_markup=contact_kb
        )
        await state.set_state(BookingStates.waiting_for_contact)
        logging.info(
            f"Пользователь {callback.from_user.id} перешёл к выбору способа ввода контакта"
        )
    except Exception as e:
        logging.error(
            f"Ошибка в ask_contact для пользователя {callback.from_user.id}: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer(f"Произошла ошибка: {str(e)}", show_alert=True)
        await state.clear()


@router.message(
    F.text == "✏️ Ввести вручную", StateFilter(BookingStates.waiting_for_contact)
)
async def prompt_manual_contact(message: Message, state: FSMContext):
    try:
        await message.answer(
            "Введите данные в таком формате✏️\n"
            "Формат: ФИО, номер телефона\n"
            "Пример: Иванов Иван Иванович, +79991234567",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        await state.set_state(BookingStates.waiting_for_manual_contact)
        logging.info(f"Пользователь {message.from_user.id} выбрал ручной ввод контакта")
    except Exception as e:
        logging.error(
            f"Ошибка в prompt_manual_contact для пользователя {message.from_user.id}: {str(e)}\n{traceback.format_exc()}"
        )
        await message.answer(f"⚠️ Ошибка: {str(e)}")


@router.message(BookingStates.waiting_for_manual_contact)
async def save_manual_contact(message: Message, state: FSMContext):
    try:
        # Удаляем лишние пробелы
        text = message.text.strip()
        # Ищем номер телефона (начинается с +7, 7, 8, 9 и содержит цифры, пробелы, дефисы, скобки)
        phone_match = re.search(r"(\+?\d[\d\s\-()]+)", text)

        if not phone_match:
            raise ValueError(
                "Номер телефона не найден. Пожалуйста, укажите ФИО и номер телефона (например, Иванов Иван, +79991234567)."
            )

        phone = phone_match.group(1).strip()
        # Удаляем номер телефона из строки, чтобы получить ФИО
        full_name = re.sub(r"\+?\d[\d\s\-()]+", "", text).strip()

        if not full_name:
            raise ValueError(
                "ФИО не указано. Пожалуйста, укажите ФИО и номер телефона (например, Иванов Иван, +79991234567)."
            )

        # Сохраняем данные без дополнительных проверок
        await state.update_data(
            full_name=full_name,
            phone=phone,
            user_id=message.from_user.id,
            username=message.from_user.username,
        )
        await save_current_booking_draft(message.from_user.id, state)
        await save_current_booking_draft(message.from_user.id, state)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✏️ Оставить комментарий", callback_data="leave_comment"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⏭ Пропустить", callback_data="skip_comment"
                    )
                ],
            ]
        )
        await message.answer(
            "Контакт принят.", reply_markup=types.ReplyKeyboardRemove()
        )
        await message.answer(
            "Хотите оставить комментарий для администратора?", reply_markup=kb
        )
        await state.set_state(BookingStates.waiting_for_comment)
        logging.info(
            f"Пользователь {message.from_user.id} отправил контактные данные вручную: ФИО='{full_name}', телефон='{phone}' и перешёл к вводу комментария"
        )
    except ValueError as e:
        logging.error(
            f"Ошибка в save_manual_contact для пользователя {message.from_user.id}: {str(e)}"
        )
        await message.answer(f"⚠️ {str(e)}")
        await state.set_state(BookingStates.waiting_for_manual_contact)
    except Exception as e:
        logging.error(
            f"Ошибка в save_manual_contact для пользователя {message.from_user.id}: {str(e)}\n{traceback.format_exc()}"
        )
        await message.answer(
            f"⚠️ Ошибка: {str(e)}\nПожалуйста, укажите ФИО и номер телефона (например, Иванов Иван, +79991234567)."
        )
        await state.set_state(BookingStates.waiting_for_manual_contact)


@router.callback_query(F.data.startswith("end_cal_"))
async def paginate_calendar_for_end(callback: CallbackQuery, state: FSMContext):
    try:
        _, year, month, room_type = callback.data.split("_")
        year, month = int(year), int(month)
        data = await state.get_data()
        duration = data.get("duration", 1)
        start_date_str = data.get("start_date")
        if not start_date_str:
            await callback.answer(
                "Ошибка: дата заезда не выбрана. Начните бронирование заново.",
                show_alert=True,
            )
            await state.clear()
            await callback.message.answer("📅 Введите количество суток (например, 2):")
            await state.set_state(BookingStates.waiting_for_duration)
            return

        start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
        booked_dates = await get_booked_dates(room_type)
        min_end_date = start_date + timedelta(days=duration)
        max_end_date = min_end_date

        # Проверяем, что переданные year и month соответствуют дате выезда
        expected_end_year = min_end_date.year
        expected_end_month = min_end_date.month
        if year != expected_end_year or month != expected_end_month:
            year = expected_end_year
            month = expected_end_month
            logging.debug(
                f"Корректировка календаря выезда: year={year}, month={month} для соответствия дате выезда {min_end_date}"
            )

        calendar = await generate_calendar(
            year=year,
            month=month,
            booked_dates=booked_dates,
            min_date=min_end_date,
            max_date=max_end_date,
            room_type=room_type,
            is_checkout_calendar=True,
            selected_start_date=start_date,
            duration=duration,
        )

        new_text = (
            f"📅 Вы выбрали проживание на {get_duration_text(duration)}. Доступная дата выезда: {min_end_date.strftime('%d.%m.%Y')} до 12:00.\n"
            f"🟢 — доступная дата выезда (на основе {get_duration_text(duration)})\n"
            f"🔴 — недоступные даты"
        )

        # Проверяем, изменился ли текст или клавиатура
        if (
            callback.message.text == new_text
            and callback.message.reply_markup == calendar
        ):
            await callback.answer()
            return

        await callback.message.edit_text(new_text, reply_markup=calendar)
        logging.info(
            f"Пользователь {callback.from_user.id} переключил календарь выезда на {year}-{month} для {room_type}"
        )
        await callback.answer()
    except Exception as e:
        logging.error(
            f"Ошибка в paginate_calendar_for_end для year {year}, month {month}: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer("Произошла ошибка. Попробуйте снова.", show_alert=True)
        await state.clear()
        await callback.message.answer("📅 Введите количество суток (например, 2):")
        await state.set_state(BookingStates.waiting_for_duration)


@router.callback_query(
    F.data.in_(["bed_yes", "bed_no"]), BookingStates.waiting_for_bed_choice
)
async def handle_bed_choice(callback: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    children = data.get("children", 0)
    current_child = data.get("current_child", 1)
    children_beds = data.get("children_beds", [0] * children)
    adults = data.get("adults", 1)

    logging.debug(
        f"Начало handle_bed_choice: user_id={callback.from_user.id}, current_child={current_child}, children={children}, children_beds={children_beds}, adults={adults}"
    )

    if current_child > len(children_beds):
        logging.error(
            f"Ошибка: current_child={current_child} превышает длину children_beds={len(children_beds)}, user_id={callback.from_user.id}"
        )
        await callback.message.edit_text(
            "⚠️ Ошибка в выборе кроватей. Начните заново.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад к выбору взрослых",
                            callback_data="back_to_adults",
                        )
                    ]
                ]
            ),
        )
        await state.set_state(BookingStates.waiting_for_adults)
        await callback.answer()
        return

    # Сохраняем выбор кровати для текущего ребёнка
    children_beds[current_child - 1] = 1 if callback.data == "bed_yes" else 0
    await state.update_data(children_beds=children_beds)

    logging.debug(
        f"Обновлено children_beds: {children_beds}, user_id={callback.from_user.id}"
    )

    # Проверяем, что данные сохранены в FSMContext
    updated_data = await state.get_data()
    logging.debug(
        f"Проверка FSMContext после обновления: children_beds={updated_data.get('children_beds')}, user_id={callback.from_user.id}"
    )

    children_needing_beds = sum(children_beds)
    total_people = adults + children_needing_beds
    MAX_TOTAL_PEOPLE = 14
    if total_people > MAX_TOTAL_PEOPLE:
        await callback.message.edit_text(
            f"⚠️ Общее количество человек (взрослых: {adults}, детей с кроватями: {children_needing_beds}) превышает максимальную вместимость ({MAX_TOTAL_PEOPLE}). "
            "Попробуйте уменьшить количество кроватей или вернуться к выбору взрослых.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад к выбору взрослых",
                            callback_data="back_to_adults",
                        )
                    ]
                ]
            ),
        )
        await state.set_state(BookingStates.waiting_for_adults)
        logging.info(
            f"Пользователь {callback.from_user.id} превысил максимальную вместимость: total_people={total_people}"
        )
        await callback.answer()
        return

    if current_child < children:
        await state.update_data(current_child=current_child + 1)
        await callback.message.edit_text(
            f"Ребёнок {current_child + 1} из {children}:\n"
            f"Нужно ли отдельное спальное место?\n"
            f"Если кровать нужна, сумма бронирования будет пересчитана: ребёнок считается как взрослый гость.\n"
            f"Если кровать не нужна, ребёнок спит с родителями и не добавляется к платным местам.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="Да, нужна кровать", callback_data="bed_yes"
                        )
                    ],
                    [InlineKeyboardButton(text="Нет", callback_data="bed_no")],
                ]
            ),
        )
        logging.debug(
            f"Perehod k rebenku {current_child + 1}, children_beds={children_beds}, user_id={callback.from_user.id}"
        )
    else:
        await callback.message.delete()
        await callback.message.answer(
            f"Выбор спальных мест завершён.\n"
            f"Детей с отдельной кроватью: {children_needing_beds}. Они будут учтены в итоговой сумме как платные места."
        )
        await state.update_data(children_beds=children_beds)
        await save_current_booking_draft(callback.from_user.id, state)
        logging.debug(
            f"Pered perehodom k select_room_type: children_beds={children_beds}, total_people={total_people}, user_id={callback.from_user.id}"
        )
        await select_room_type(callback.message, state)
        logging.info(
            f"Polzovatel {callback.from_user.id} zavershil vybor krovatyey dlya {children} detey, children_needing_beds={children_needing_beds}, total_people={total_people}"
        )

    await callback.answer()


async def generate_calendar(
    year: int,
    month: int,
    booked_dates: Tuple[List[date], List[date]],
    min_date: date = None,
    max_date: date = None,
    room_type: str = None,
    is_checkout_calendar: bool = False,
    selected_start_date: date = None,
    duration: int = 1,
    is_admin_mode: bool = False,
    state: FSMContext = None,
) -> InlineKeyboardMarkup:
    try:
        occupied_dates, checkin_dates = booked_dates
        first_day = datetime(year, month, 1)
        last_day = (first_day + timedelta(days=31)).replace(day=1) - timedelta(days=1)
        today = date.today()

        # Получаем текущий год бронирования
        with session as db_session:
            booking_year = db_session.query(BookingYear).first()
            current_booking_year = booking_year.year if booking_year else 2026

        russian_months = {
            1: "Январь",
            2: "Февраль",
            3: "Март",
            4: "Апрель",
            5: "Май",
            6: "Июнь",
            7: "Июль",
            8: "Август",
            9: "Сентябрь",
            10: "Октябрь",
            11: "Ноябрь",
            12: "Декабрь",
        }

        kb = []
        month_name = russian_months.get(month, "Неизвестный месяц")
        row = [
            InlineKeyboardButton(text=f"{month_name} {year}", callback_data="ignore")
        ]
        kb.append(row)

        row = [
            InlineKeyboardButton(text="Пн", callback_data="ignore"),
            InlineKeyboardButton(text="Вт", callback_data="ignore"),
            InlineKeyboardButton(text="Ср", callback_data="ignore"),
            InlineKeyboardButton(text="Чт", callback_data="ignore"),
            InlineKeyboardButton(text="Пт", callback_data="ignore"),
            InlineKeyboardButton(text="Сб", callback_data="ignore"),
            InlineKeyboardButton(text="Вс", callback_data="ignore"),
        ]
        kb.append(row)

        row = []
        weekday = first_day.weekday()
        for _ in range(weekday):
            row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))

        current_day = first_day
        while current_day <= last_day:
            day_date = current_day.date()
            is_available = False

            # Блокируем только даты до текущей в текущем году
            if day_date.year < today.year or (
                day_date.year == today.year and day_date < today
            ):
                button_text = f"{current_day.day}🔒"
                callback_data = "locked"
            else:
                if is_checkout_calendar:
                    if (
                        selected_start_date
                        and day_date == selected_start_date + timedelta(days=duration)
                    ):
                        is_available = await is_room_available(
                            room_type,
                            selected_start_date,
                            day_date - timedelta(days=1),
                            [d.isoformat() for d in occupied_dates],
                            [d.isoformat() for d in checkin_dates],
                        )
                else:
                    end_date = day_date + timedelta(days=duration - 1)
                    if (not min_date or day_date >= min_date) and (
                        not max_date or day_date <= max_date
                    ):
                        is_available = await is_room_available(
                            room_type,
                            day_date,
                            end_date,
                            [d.isoformat() for d in occupied_dates],
                            [d.isoformat() for d in checkin_dates],
                        )

                button_text = f"{current_day.day}{'🟢' if is_available else '🔴'}"
                if is_available:
                    callback_data = f"day_{year}_{month}_{current_day.day}"
                else:
                    callback_data = (
                        f"show_bookings_{year}_{month}_{current_day.day}_{room_type}"
                        if is_admin_mode
                        else "occupied"
                    )

            logging.debug(
                f"Дата {day_date} в режиме админа={is_admin_mode}: text={button_text}, callback={callback_data}"
            )

            row.append(
                InlineKeyboardButton(text=button_text, callback_data=callback_data)
            )

            if len(row) == 7:
                kb.append(row)
                row = []
            current_day += timedelta(days=1)

        if row:
            while len(row) < 7:
                row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            kb.append(row)

        prev_month = first_day - timedelta(days=1)
        next_month = last_day + timedelta(days=1)
        nav_row = []
        allowed_months = [6, 7, 8]
        if not is_admin_mode:
            # Для гостей навигация только в пределах текущего года
            if (
                prev_month.month in allowed_months
                and prev_month.year == current_booking_year
            ):
                nav_row.append(
                    InlineKeyboardButton(
                        text=russian_months[prev_month.month],
                        callback_data=f"month_{prev_month.year}_{prev_month.month}_{room_type}",
                    )
                )
            else:
                nav_row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            nav_row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            if (
                next_month.month in allowed_months
                and next_month.year == current_booking_year
            ):
                nav_row.append(
                    InlineKeyboardButton(
                        text=russian_months[next_month.month],
                        callback_data=f"month_{next_month.year}_{next_month.month}_{room_type}",
                    )
                )
            else:
                nav_row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
        else:
            # Для админа навигация по летним месяцам, без ограничений по годам
            if prev_month.month in allowed_months:
                nav_row.append(
                    InlineKeyboardButton(
                        text=russian_months[prev_month.month],
                        callback_data=f"month_{prev_month.year}_{prev_month.month}_{room_type}",
                    )
                )
            else:
                nav_row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            nav_row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
            if next_month.month in allowed_months:
                nav_row.append(
                    InlineKeyboardButton(
                        text=russian_months[next_month.month],
                        callback_data=f"month_{next_month.year}_{next_month.month}_{room_type}",
                    )
                )
            else:
                nav_row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
        kb.append(nav_row)

        if is_admin_mode:
            kb.append([InlineKeyboardButton(text="⬅️ Меню ", callback_data="admin")])
        else:
            kb.append(
                [
                    InlineKeyboardButton(
                        text="⬅️ Выберите другой номер, если нет нужной даты",
                        callback_data="select_room_type",
                    )
                ]
            )

        markup = InlineKeyboardMarkup(inline_keyboard=kb)
        logging.info(
            f"Календарь сгенерирован: year={year}, month={month}, room_type={room_type}, is_checkout_calendar={is_checkout_calendar}, is_admin_mode={is_admin_mode}"
        )
        return markup
    except Exception as e:
        logging.error(f"Ошибка в generate_calendar: {str(e)}\n{traceback.format_exc()}")
        raise


@router.message(
    F.content_type == "contact", StateFilter(BookingStates.waiting_for_contact)
)
async def save_contact(message: Message, state: FSMContext):
    try:
        full_name = (
            f"{message.from_user.first_name} {message.from_user.last_name or ''}"
        )
        phone = message.contact.phone_number
        await state.update_data(
            full_name=full_name,
            phone=phone,
            user_id=message.from_user.id,
            username=message.from_user.username,
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✏️ Оставить комментарий", callback_data="leave_comment"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⏭ Пропустить", callback_data="skip_comment"
                    )
                ],
            ]
        )
        # Удаляем клавиатуру
        await message.answer(
            "Контакт принят.", reply_markup=types.ReplyKeyboardRemove()
        )
        # Отправляем сообщение с инлайн-кнопками
        await message.answer(
            "Хотите оставить комментарий для администратора?", reply_markup=kb
        )
        await state.set_state(BookingStates.waiting_for_comment)
        logging.info(
            f"Пользователь {message.from_user.id} отправил контакт через кнопку и перешёл к вводу комментария"
        )
    except Exception as e:
        logging.error(
            f"Ошибка в save_contact для пользователя {message.from_user.id}: {str(e)}\n{traceback.format_exc()}"
        )
        await message.answer(f"⚠️ Ошибка: {str(e)}")


@router.callback_query(F.data == "leave_comment")
async def prompt_comment(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text("✍️ Введите ваш комментарий или вопрос:")
    await state.set_state(BookingStates.waiting_for_comment_text)


@router.callback_query(F.data == "skip_comment")
async def skip_comment(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    if "children_beds" not in data:
        logging.error(
            f"children_beds отсутствует в состоянии перед skip_comment, user_id={callback.from_user.id}"
        )
        await callback.message.edit_text(
            "❌ Ошибка: данные о кроватях отсутствуют. Начните заново."
        )
        await state.clear()
        await callback.message.answer("📅 Введите количество суток (например, 2):")
        await state.set_state(BookingStates.waiting_for_duration)
        await callback.answer()
        return
    await state.update_data(comment="Без комментария")
    await save_current_booking_draft(callback.from_user.id, state)
    await callback.message.edit_text("Комментарий пропущен. Отправка бронирования...")
    await complete_booking(callback.message, state)


@router.message(BookingStates.waiting_for_comment_text)
async def save_comment_text(message: Message, state: FSMContext):
    await state.update_data(comment=message.text)
    await save_current_booking_draft(message.from_user.id, state)
    await message.answer("Комментарий сохранён. Отправка заявки...")
    await complete_booking(message, state)


async def complete_booking(message: types.Message, state: FSMContext):
    try:
        data = await state.get_data()
        required_keys = [
            "user_id",
            "room_type",
            "start_date",
            "end_date",
            "adults",
            "children",
            "children_beds",
            "full_name",
            "phone",
            "comment",
        ]
        missing_keys = [key for key in required_keys if key not in data]
        if missing_keys:
            logging.error(
                f"complete_booking: отсутствуют ключи в state.get_data(): {missing_keys}, user_id={message.from_user.id}"
            )
            await message.answer(
                "❌ Ошибка: неполные данные бронирования. Начните заново."
            )
            await state.clear()
            await message.answer("📅 Введите количество суток (например, 2):")
            await state.set_state(BookingStates.waiting_for_duration)
            return

        date_from = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
        date_to = datetime.strptime(data["end_date"], "%Y-%m-%d").date()
        children_beds = data.get("children_beds", [])
        logging.debug(
            f"Перед сохранением в complete_booking: children_beds={children_beds}, children={data['children']}, user_id={data['user_id']}"
        )

        # Проверка корректности children_beds
        if not isinstance(children_beds, list):
            logging.error(
                f"children_beds не является списком: {children_beds}, user_id={data['user_id']}"
            )
            children_beds = [0] * data["children"]
        if len(children_beds) != data["children"]:
            logging.warning(
                f"Длина children_beds ({len(children_beds)}) не соответствует количеству детей ({data['children']}), user_id={data['user_id']}"
            )
            # Не перезаписываем, если children_beds уже содержит данные
            if not any(
                children_beds
            ):  # Проверяем, что список не содержит ненулевых значений
                children_beds = [0] * data["children"]

        children_needing_beds = sum(children_beds)
        total_people = data["adults"] + children_needing_beds
        total_price = await calculate_revenue(
            data["room_type"], total_people, date_from, date_to
        )
        logging.debug(
            f"complete_booking: user_id={data['user_id']}, room_type={data['room_type']}, adults={data['adults']}, "
            f"children={data['children']}, children_beds={children_beds}, children_needing_beds={children_needing_beds}, "
            f"total_people={total_people}, total_price={total_price}₽"
        )

        booking_data = {
            "user_id": data["user_id"],
            "username": data.get("username", ""),
            "full_name": data["full_name"],
            "phone": data["phone"],
            "comment": data["comment"],
            "date_from": date_from,
            "date_to": date_to,
            "room_type": data["room_type"],
            "adults": data["adults"],
            "children": data["children"],
            "status": "new",
            "children_beds": json.dumps(
                children_beds
            ),  # Всегда сохраняем как JSON, даже если пустой список
        }
        valid_columns = {c.name for c in Booking.__table__.columns}
        booking_data = {k: v for k, v in booking_data.items() if k in valid_columns}
        logging.debug(f"booking_data перед сохранением: {booking_data}")

        booking = Booking(**booking_data)
        session.add(booking)
        session.commit()

        # Проверяем, что children_beds сохранено корректно
        session.refresh(booking)
        saved_children_beds = get_children_beds(booking)
        logging.debug(
            f"Сохранено в базе для заявки #{booking.id}: children_beds={saved_children_beds}"
        )
        if saved_children_beds != children_beds:
            logging.error(
                f"Несоответствие children_beds для заявки #{booking.id}: ожидалось {children_beds}, сохранено {saved_children_beds}"
            )
            raise ValueError(
                f"Ошибка сохранения children_beds для заявки #{booking.id}"
            )

        clear_booked_dates_cache()
        logging.info(
            f"Кэш booked_dates_cache очищен после создания заявки #{booking.id}"
        )

        comment_text = data["comment"]
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💬 Ответить", callback_data=f"admin_reply_{booking.id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="✅ Подтвердить",
                        callback_data=f"admin_confirm_{booking.id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Отклонить", callback_data=f"admin_reject_{booking.id}"
                    )
                ],
            ]
        )
        await message.bot.send_message(
            ADMIN_CHAT_ID,
            f"📌 Новая заявка #{booking.id}\n"
            f"👤 Клиент: {data['full_name']} (@{data.get('username', 'без ника')})\n"
            f"📞 Телефон: {data['phone']}\n"
            f"🏠 Номер: {room_type_names[data['room_type']]}\n"
            f"📅 Заезд: {date_from.strftime('%d.%m.%Y')} после 14:00\n"
            f"📅 Выезд: {date_to.strftime('%d.%m.%Y')} до 12:00\n"
            f"👨‍👩‍👧‍👦 Всего человек: {total_people} (взрослых: {data['adults']}, детей: {data['children']}, из них {children_needing_beds} с местами)\n"
            f"💰 Сумма: {total_price}₽\n"
            f"💬 Комментарий: {comment_text}",
            reply_markup=kb,
        )
        await message.answer(
            "✅ Заявка отправлена администратору. Ожидайте подтверждения.",
            reply_markup=types.ReplyKeyboardRemove(),
        )
        await state.update_data(
            support_user_id=data["user_id"],
            booking_id=booking.id,
            support_context="booking",
        )
        logging.info(
            f"Заявка #{booking.id} создана для пользователя {data['user_id']}, сохранён контекст поддержки (booking), сумма={total_price}₽"
        )
        clear_booking_draft(data["user_id"])
    except Exception as e:
        logging.error(f"Ошибка в complete_booking: {str(e)}\n{traceback.format_exc()}")
        await message.answer(f"❌ Ошибка при сохранении: {str(e)}")
    finally:
        await state.clear()


@router.callback_query(F.data.startswith("admin_reply_"))
async def admin_reply(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    booking_id = int(callback.data.split("_")[2])
    booking = session.query(Booking).filter_by(id=booking_id).first()
    if not booking:
        await callback.message.answer("Ошибка: заявка не найдена.")
        return
    await state.update_data(
        support_user_id=booking.user_id,
        booking_id=booking_id,
        support_context="booking",
    )
    await callback.message.answer("Введите ответ для пользователя:")
    await state.set_state(BookingStates.waiting_for_admin_response)
    session.add(
        AdminLog(
            admin_id=callback.from_user.id,
            action=f"Начал ответ на заявку #{booking_id}",
            booking_id=booking_id,
        )
    )
    session.commit()
    logging.debug(
        f"Администратор {callback.from_user.id} начал ответ на заявку #{booking_id} для пользователя {booking.user_id}"
    )


def close_open_support_ticket(user_id: int, booking_id: int | None, status: str):
    ticket = (
        session.query(SupportTicket)
        .filter(
            SupportTicket.user_id == user_id,
            SupportTicket.status == "open",
            SupportTicket.booking_id == booking_id,
        )
        .order_by(SupportTicket.id.desc())
        .first()
    )
    if ticket:
        ticket.status = status
        ticket.closed_at = datetime.now()
        session.commit()
    return ticket


@router.callback_query(F.data == "admin_support_tickets")
async def admin_support_tickets(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав", show_alert=True)
        return

    now = datetime.now()
    tickets = (
        session.query(SupportTicket)
        .filter(SupportTicket.status == "open")
        .order_by(SupportTicket.expires_at.asc())
        .limit(20)
        .all()
    )
    if not tickets:
        await callback.message.edit_text(
            "💬 Открытых обращений поддержки нет.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="⬅️ Админка", callback_data="admin")]]
            ),
        )
        await callback.answer()
        return

    await callback.message.delete()
    for ticket in tickets:
        booking = (
            session.query(Booking).filter_by(id=ticket.booking_id).first()
            if ticket.booking_id
            else None
        )
        last_message = (
            session.query(SupportMessage)
            .filter(
                SupportMessage.user_id == ticket.user_id,
                SupportMessage.booking_id == ticket.booking_id,
            )
            .order_by(SupportMessage.id.desc())
            .first()
        )
        minutes_left = max(0, int((ticket.expires_at - now).total_seconds() // 60))
        text = (
            f"💬 Обращение #{ticket.id}\n"
            f"👤 Пользователь: {ticket.user_id}\n"
            f"📌 Заявка: #{ticket.booking_id or 'без заявки'}\n"
            f"⏳ Автозакрытие через: {minutes_left} мин.\n"
        )
        if booking:
            text += (
                f"🏠 Номер: {room_type_names.get(booking.room_type, booking.room_type)}\n"
                f"📅 Даты: {booking.date_from.strftime('%d.%m.%Y')} - {booking.date_to.strftime('%d.%m.%Y')}\n"
            )
        if last_message:
            direction = "админ" if last_message.from_admin else "гость"
            text += f"\nПоследнее сообщение ({direction}):\n{last_message.message[:700]}"

        await callback.message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💬 Ответить",
                            callback_data=f"support_reply_{ticket.user_id}_{ticket.booking_id or ''}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="✅ Закрыть",
                            callback_data=f"admin_close_support_{ticket.id}",
                        )
                    ],
                ]
            ),
        )
    await callback.message.answer(
        "Список открытых обращений.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ Админка", callback_data="admin")]]
        ),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_close_support_"))
async def admin_close_support_ticket(callback: CallbackQuery):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав", show_alert=True)
        return
    ticket_id = int(callback.data.rsplit("_", 1)[1])
    ticket = session.query(SupportTicket).filter_by(id=ticket_id).first()
    if not ticket:
        await callback.answer("Обращение не найдено", show_alert=True)
        return
    ticket.status = "closed_admin"
    ticket.closed_at = datetime.now()
    session.add(AdminLog(admin_id=callback.from_user.id, action=f"Закрыл обращение поддержки #{ticket.id}"))
    session.commit()
    try:
        await callback.message.bot.send_message(ticket.user_id, "✅ Ваш вопрос закрыт администратором.")
    except Exception as e:
        logging.warning("Failed to notify user about support ticket close #%s: %s", ticket.id, e)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer("Обращение закрыто")


@router.message(BookingStates.waiting_for_admin_response)
async def send_admin_response(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        await message.answer("У вас нет прав на это действие.")
        return
    data = await state.get_data()
    support_user_id = data.get("support_user_id")
    booking_id = data.get("booking_id")
    support_context = data.get("support_context", "general")

    # Проверяем наличие заявки
    booking = (
        session.query(Booking).filter_by(id=booking_id).first() if booking_id else None
    )

    # Если support_user_id отсутствует, пытаемся взять из booking
    if not support_user_id and booking:
        support_user_id = booking.user_id

    if not support_user_id:
        await message.answer("Ошибка: пользователь не найден.")
        await state.clear()
        return

    try:
        # Определяем заголовок по контексту
        context_headers = {
            "general": "💬 Ответ от администратора",
            "payment": f"💸 Ответ по оплате заявки #{booking_id}",
            "booking": f"📌 Ответ по заявке #{booking_id}",
            "reply": f"💬 Ответ от администратора по заявке #{booking_id or 'без ID'}",
        }
        header = context_headers.get(support_context, "💬 Ответ от администратора")

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💬 Ответить",
                        callback_data=f"user_support_reply_{support_user_id}_{booking_id or ''}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="✅ Вопрос решён",
                        callback_data=f"user_resolved_support_{support_user_id}_{booking_id or ''}",
                    )
                ],
            ]
        )
        if booking and booking.status == "awaiting_payment":
            kb.inline_keyboard.insert(
                0,
                [
                    InlineKeyboardButton(
                        text="❌ Отменить заявку",
                        callback_data=f"cancel_booking_{booking.id}",
                    ),
                    InlineKeyboardButton(
                        text="📸 Отправить скриншот",
                        callback_data=f"send_screenshot_{booking.id}",
                    ),
                ],
            )

        user_message = f"{header}:\n{message.text}"
        if booking:
            user_message = (
                f"{header}:\n"
                f"👤 {booking.full_name} (@{booking.username or 'без ника'})\n"
                f"📞 {booking.phone}\n"
                f"🏠 {booking.room_type.replace('+', ' + ')}-местный\n"
                f"📅 Заезд: {booking.date_from.strftime('%d.%m.%Y')} после 14:00\n"
                f"📅 Выезд: {booking.date_to.strftime('%d.%m.%Y')} до 12:00\n\n"
                f"{message.text}"
            )

        close_open_support_ticket(support_user_id, booking_id, "closed_replaced")
        sent_message = await message.bot.send_message(support_user_id, user_message, reply_markup=kb)
        session.add(
            SupportTicket(
                user_id=support_user_id,
                booking_id=booking_id,
                user_message_id=sent_message.message_id,
                status="open",
                expires_at=datetime.now() + timedelta(minutes=15),
            )
        )
        session.commit()
        await message.answer("Ответ отправлен пользователю. Если гость не нажмет кнопку, вопрос закроется через 15 минут.")
        session.add(
            AdminLog(
                admin_id=message.from_user.id,
                action=f"Отправил ответ в поддержку пользователю {support_user_id} (контекст: {support_context}, заявка #{booking_id or 'без ID'})",
            )
        )
        session.commit()
        logging.info(
            f"Администратор {message.from_user.id} отправил ответ пользователю {support_user_id} (контекст: {support_context}, заявка #{booking_id or 'без ID'})"
        )
    except Exception as e:
        logging.error(
            f"Ошибка в send_admin_response для user_id {support_user_id}, booking_id {booking_id or 'без ID'}, context={support_context}: {str(e)}"
        )
        await message.answer(f"❗ Ошибка при отправке сообщения пользователю: {str(e)}")
        session.add(
            AdminLog(
                admin_id=message.from_user.id,
                action=f"Ошибка при отправке ответа: {str(e)}",
            )
        )
        session.commit()
    finally:
        await state.clear()


@router.callback_query(F.data.startswith("user_resolved_support_"))
async def user_resolved_support(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    user_id = int(parts[3])
    booking_id = int(parts[4]) if len(parts) > 4 and parts[4] else None
    if callback.from_user.id != user_id:
        await callback.answer("У вас нет прав на это действие.", show_alert=True)
        return

    data = await state.get_data()
    support_context = data.get("support_context", "general")

    # Определяем контекст для уведомления администратора
    context_headers = {
        "general": "общее обращение",
        "payment": f"вопрос по оплате заявки #{booking_id}",
        "booking": f"вопрос по заявке #{booking_id}",
        "reply": f"переписку по заявке #{booking_id or 'без ID'}",
    }
    context_description = context_headers.get(support_context, "общее обращение")

    try:
        close_open_support_ticket(user_id, booking_id, "closed_resolved")
        # Удаляем кнопки из последнего сообщения
        await callback.message.edit_reply_markup(reply_markup=None)

        # Проверяем, связано ли обращение с оплатой
        booking = (
            session.query(Booking)
            .filter(
                Booking.id == booking_id,
                Booking.user_id == user_id,
                Booking.status.in_(
                    ["awaiting_payment", "awaiting_payment_confirmation"]
                ),
            )
            .first()
            if booking_id
            else None
        )
        if booking:
            await state.update_data(
                booking_id=booking_id, support_context=support_context
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="❌ Отменить заявку",
                            callback_data=f"cancel_booking_{booking.id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            text="📸 Отправить скриншот",
                            callback_data=f"send_screenshot_{booking.id}",
                        )
                    ],
                ]
            )
            await callback.message.answer(
                f"Спасибо, ваш вопрос по заявке #{booking_id} отмечен как решённым.\n"
                f"Продолжить оплату или отменить заявку?",
                reply_markup=kb,
            )
            logging.info(
                f"Пользователь {user_id} отметил вопрос решённым для заявки #{booking_id}, предложены действия (контекст: {support_context})"
            )
        else:
            await callback.message.answer("Спасибо, ваш вопрос отмечен как решённый.")
            logging.info(
                f"Пользователь {user_id} отметил вопрос решённым, заявка не найдена (контекст: {support_context})"
            )

        # Уведомляем администратора
        await callback.message.bot.send_message(
            ADMIN_CHAT_ID,
            f"📌 Пользователь @{callback.from_user.username or 'без ника'} (ID: {user_id}) отметил, что {context_description} решён.",
        )
    except Exception as e:
        logging.error(
            f"Ошибка в user_resolved_support для user_id {user_id}, booking_id {booking_id or 'без ID'}, context={support_context}: {str(e)}"
        )
        await callback.message.answer(f"❗ Ошибка при обработке: {str(e)}")
    finally:
        await state.clear()
        await callback.answer()


async def contact_handler(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    room_type = data.get("room_type")
    date_from = data.get("date_from")
    date_to = data.get("date_to")
    adults = data.get("adults", 0)
    children = data.get("children", 0)
    children_with_parents = data.get("children_with_parents", 0)
    total_people = adults + (children - children_with_parents)

    booking = Booking(
        user_id=user_id,
        room_type=room_type,
        date_from=date_from,
        date_to=date_to,
        adults=adults,
        children=children,
        children_with_parents=children_with_parents,
        status=BookingStatus.PENDING.value,
        total_price=await calculate_revenue(
            room_type, total_people, date_from, date_to
        ),
    )
    session.add(booking)
    session.commit()
    logging.info(
        f"Заявка #{booking.id} создана для пользователя {user_id}, сохранён контекст поддержки (booking)"
    )

    booking.status = BookingStatus.AWAITING_PAYMENT.value
    booking.payment_deadline = datetime.now() + timedelta(
        hours=PAYMENT_DEADLINE_HOURS
    )  # Исправлено
    session.commit()
    logging.info(
        f"Заявка #{booking.id} перешла в awaiting_payment, срок оплаты до {booking.payment_deadline}"
    )

    await message.answer(
        f"Заявка #{booking.id} создана! Пожалуйста, оплатите {booking.total_price}₽ до {booking.payment_deadline.strftime('%d.%m.%Y %H:%M')}."
    )
    await state.clear()


@router.callback_query(F.data.startswith("continue_payment_"))
async def continue_payment(callback: CallbackQuery, state: FSMContext):
    booking_id = int(callback.data.split("_")[2])
    booking = (
        session.query(Booking)
        .filter_by(
            id=booking_id, user_id=callback.from_user.id, status=BookingStatus.AWAITING_PAYMENT.value
        )
        .first()
    )
    if not booking:
        await callback.answer(
            "У вас нет доступа к этой заявке или заявка не ожидает оплаты.",
            show_alert=True,
        )
        return
    await state.update_data(booking_id=booking_id)
    await callback.answer("Теперь прикрепите фото чека из галереи.")
    await prompt_payment_screenshot(callback.message, booking_id)
    await state.set_state(BookingStates.waiting_for_payment_screenshot)
    logging.info(
        f"Пользователь {callback.from_user.id} выбрал продолжение оплаты для заявки #{booking_id}"
    )


async def prompt_payment_screenshot(message: Message, booking_id: int):
    await message.answer(
        (
            f"📸 Прикрепите фото чека для заявки #{booking_id}.\n\n"
            "Как выбрать из галереи:\n"
            "1. Нажмите скрепку/плюс рядом с полем ввода.\n"
            "2. Выберите «Галерея» или «Фото».\n"
            "3. Отправьте изображение сюда.\n\n"
            "Telegram не позволяет боту открыть галерею автоматически, "
            "но после этого сообщения бот уже ждёт именно фото."
        ),
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Прикрепите фото чека из галереи",
        ),
    )


@router.callback_query(F.data.startswith("send_screenshot_"))
async def send_screenshot(callback: CallbackQuery, state: FSMContext):
    booking_id = int(callback.data.split("_")[2])
    booking = (
        session.query(Booking)
        .filter_by(
            id=booking_id, user_id=callback.from_user.id, status=BookingStatus.AWAITING_PAYMENT.value
        )
        .first()
    )
    if not booking:
        await callback.answer(
            "У вас нет доступа к этой заявке или заявка не ожидает оплаты.",
            show_alert=True,
        )
        return
    await state.update_data(booking_id=booking_id)
    await callback.answer("Теперь прикрепите фото чека из галереи.")
    await prompt_payment_screenshot(callback.message, booking_id)
    await state.set_state(BookingStates.waiting_for_payment_screenshot)
    logging.info(
        f"Пользователь {callback.from_user.id} начал отправку скриншота для заявки #{booking_id}"
    )


@router.callback_query(F.data.startswith("user_reply_"))
async def user_reply(callback: CallbackQuery, state: FSMContext):
    booking_id = int(callback.data.split("_")[2])
    booking = session.query(Booking).filter_by(id=booking_id).first()
    if not booking or booking.user_id != callback.from_user.id:
        await callback.answer("У вас нет доступа к этой заявке.", show_alert=True)
        await state.clear()
        return
    await state.update_data(
        support_user_id=callback.from_user.id,
        booking_id=booking_id,
        support_context="booking",
    )
    await callback.message.answer("Введите ваш вопрос или комментарий по заявке:")
    await state.set_state(BookingStates.waiting_for_support_message)
    logging.debug(
        f"Пользователь {callback.from_user.id} начал обращение в поддержку по заявке #{booking_id}"
    )


@router.callback_query(F.data.startswith("support_reply_"))
async def support_reply(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    parts = callback.data.split("_")
    user_id = int(parts[2])
    booking_id = int(parts[3]) if len(parts) > 3 and parts[3] else None
    await state.update_data(
        support_user_id=user_id, booking_id=booking_id, support_context="reply"
    )
    await callback.message.answer("Введите ваш ответ или вопрос:")
    await state.set_state(BookingStates.waiting_for_admin_response)
    session.add(
        AdminLog(
            admin_id=callback.from_user.id,
            action=f"Начал ответ в поддержку пользователю {user_id} по заявке #{booking_id or 'без ID'}",
        )
    )
    session.commit()
    logging.debug(
        f"Администратор {callback.from_user.id} начал ответ пользователю {user_id} (заявка #{booking_id or 'без ID'}, контекст: reply)"
    )


@router.callback_query(F.data.startswith("user_support_reply_"))
async def user_support_reply(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_")
    user_id = int(parts[3])
    booking_id = int(parts[4]) if len(parts) > 4 and parts[4] else None
    if callback.from_user.id != user_id:
        await callback.answer("У вас нет прав на это действие.", show_alert=True)
        return
    close_open_support_ticket(user_id, booking_id, "closed_reply")
    try:
        await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest:
        pass
    await state.update_data(
        support_user_id=user_id, booking_id=booking_id, support_context="reply"
    )
    await callback.message.answer("Введите ваш ответ или новый вопрос:")
    await state.set_state(BookingStates.waiting_for_support_message)
    await callback.answer()
    logging.debug(
        "User %s started support reply for booking #%s",
        user_id,
        booking_id or "без ID",
    )


@router.message(BookingStates.waiting_for_user_response)
async def send_user_response(message: Message, state: FSMContext):
    data = await state.get_data()
    booking_id = data["booking_id"]
    booking = session.query(Booking).filter_by(id=booking_id).first()
    if booking:
        try:
            await message.bot.send_message(
                ADMIN_CHAT_ID,
                f"📬 Ответ от пользователя (@{booking.username or 'без ника'}):\n{message.text}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="💬 Ответить",
                                callback_data=f"admin_reply_{booking_id}",
                            )
                        ]
                    ]
                ),
            )
            await message.answer("Ваш ответ отправлен администратору.")
        except Exception as e:
            logging.error(
                f"Ошибка в send_user_response для booking_id {booking_id}: {str(e)}"
            )
            await message.answer("❗ Ошибка при отправке ответа. Попробуйте снова.")
    await state.clear()


@router.callback_query(F.data == "cancel")
async def cancel_booking(callback: CallbackQuery, state: FSMContext):
    logging.info(f"Пользователь {callback.from_user.id} отменил бронирование")
    clear_booking_draft(callback.from_user.id)
    await state.clear()
    await callback.message.answer("Бронирование отменено")
    await cmd_start(callback.message)


@router.callback_query(F.data == "cancel_booking")
async def cancel_booking_callback(callback: CallbackQuery, state: FSMContext):
    from utils import clear_booked_dates_cache

    logging.debug(
        f"Обработка callback: cancel_booking, data={callback.data}, user={callback.from_user.id}, message_id={callback.message.message_id}"
    )
    try:
        clear_booking_draft(callback.from_user.id)
        await state.clear()
        await callback.message.edit_text("❌ Бронирование отменено", reply_markup=None)
        clear_booked_dates_cache()
        logging.info(f"Пользователь {callback.from_user.id} отменил бронирование")
        await callback.answer()
    except Exception as e:
        logging.error(
            f"Ошибка в cancel_booking_callback: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer(f"Произошла ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data.regexp(r"admin_reject_(\d+)"))
async def reject_booking_callback(callback: CallbackQuery):
    from utils import clear_booked_dates_cache

    try:
        if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
            await callback.answer("У вас нет прав на это действие", show_alert=True)
            return
        booking_id = int(callback.data.split("_")[2])
        booking = session.query(Booking).filter_by(id=booking_id).first()
        if not booking:
            await callback.answer(f"Заявка #{booking_id} не найдена.", show_alert=True)
            return
        booking.status = BookingStatus.REJECTED.value
        session.commit()
        clear_booked_dates_cache()
        logging.info(
            f"Кэш booked_dates_cache очищен после отклонения заявки #{booking_id}"
        )
        try:
            if booking.user_id:
                await callback.message.bot.send_message(
                    chat_id=booking.user_id,
                    text=(
                        f"❌ Ваша заявка #{booking.id} отклонена администратором.\n"
                        f"Для уточнения деталей свяжитесь с {ADMIN_USERNAME}"
                    ),
                )
        except Exception as e:
            await callback.message.answer(
                f"❗ Ошибка при уведомлении пользователя: {e}"
            )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"Заявка #{booking.id} отклонена ❌")
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Отклонил заявку #{booking_id}",
                booking_id=booking_id,
            )
        )
        session.commit()
        logging.info(
            f"Заявка #{booking.id} отклонена администратором {callback.from_user.id}"
        )
    except ValueError:
        await callback.answer("Неверный формат данных заявки.", show_alert=True)
    except Exception as e:
        logging.error(
            f"Ошибка в reject_booking_callback: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer(f"Произошла ошибка: {str(e)}", show_alert=True)


@router.message(F.photo, StateFilter(BookingStates.waiting_for_payment_screenshot))
async def handle_payment_photo(message: Message, state: FSMContext):
    user_id = message.from_user.id
    data = await state.get_data()
    booking_id = data.get("booking_id")
    if booking_id:
        booking = (
            session.query(Booking)
            .filter_by(id=booking_id, user_id=user_id, status=BookingStatus.AWAITING_PAYMENT.value)
            .first()
        )
    else:
        booking = (
            session.query(Booking)
            .filter_by(user_id=user_id, status=BookingStatus.AWAITING_PAYMENT.value)
            .order_by(Booking.id.desc())
            .first()
        )
    if not booking:
        await message.answer("❌ Нет активной заявки, ожидающей оплаты.")
        await state.clear()
        return
    photo = message.photo[-1]
    file_id = photo.file_id
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтвердить оплату",
                    callback_data=f"admin_paid_{booking.id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Оплата не прошла",
                    callback_data=f"admin_payment_failed_{booking.id}",
                )
            ],
        ]
    )
    try:
        await message.bot.send_photo(
            chat_id=ADMIN_CHAT_ID,
            photo=file_id,
            caption=(
                f"📥 Скриншот оплаты от пользователя:\n"
                f"👤 {booking.full_name} (@{booking.username or 'без ника'})\n"
                f"🆔 ID: <code>{user_id}</code>\n"
                f"📌 Заявка #{booking.id}"
            ),
            reply_markup=kb,
            parse_mode="HTML",
        )
        # Меняем статус на awaiting_payment_confirmation
        booking.status = BookingStatus.AWAITING_PAYMENT_CONFIRMATION.value
        session.commit()
        await message.answer(
            "✅ Скриншот отправлен администратору. Ожидайте подтверждения."
        )
        await state.clear()
        logging.info(
            f"Пользователь {user_id} отправил скриншот для заявки #{booking.id}, статус изменён на awaiting_payment_confirmation"
        )
    except Exception as e:
        logging.error(
            f"Ошибка в handle_payment_photo для booking_id {booking.id}: {str(e)}"
        )
        await message.answer("❗ Ошибка при отправке скриншота. Попробуйте снова.")


@router.message(StateFilter(BookingStates.waiting_for_payment_screenshot))
async def handle_payment_not_photo(message: Message, state: FSMContext):
    data = await state.get_data()
    booking_id = data.get("booking_id")
    await message.answer(
        (
            f"Жду именно фото чека{f' для заявки #{booking_id}' if booking_id else ''}.\n\n"
            "Нажмите скрепку/плюс рядом с полем ввода, выберите «Галерея» "
            "или «Фото» и отправьте изображение."
        ),
        reply_markup=ForceReply(
            selective=True,
            input_field_placeholder="Прикрепите фото чека из галереи",
        ),
    )


@router.callback_query(F.data.regexp(r"admin_payment_failed_(\d+)"))
async def payment_failed_callback(callback: CallbackQuery):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    try:
        booking_id = int(callback.data.split("_")[3])
        booking = session.query(Booking).filter_by(id=booking_id).first()
        if not booking:
            await callback.answer(f"Заявка #{booking_id} не найдена.", show_alert=True)
            return
        # Возвращаем статус awaiting_payment и устанавливаем новый payment_deadline
        booking.status = BookingStatus.AWAITING_PAYMENT.value
        booking.payment_deadline = datetime.now() + timedelta(
            hours=PAYMENT_DEADLINE_HOURS
        )
        session.commit()
        try:
            if booking.user_id:
                await callback.message.bot.send_message(
                    chat_id=booking.user_id,
                    text=(
                        f"❌ Оплата по заявке #{booking.id} не подтверждена.\n"
                        f"Пожалуйста, отправьте корректный скриншот до {booking.payment_deadline.strftime('%d.%m.%Y %H:%M')}.\n"
                        f"Свяжитесь с администратором: {ADMIN_USERNAME}"
                    ),
                )
        except Exception as e:
            await callback.message.answer(
                f"❗ Ошибка при уведомлении пользователя: {e}"
            )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"Пользователь уведомлён об ошибке оплаты для заявки #{booking.id}. Новый срок оплаты: {booking.payment_deadline}"
        )
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Отметил ошибку оплаты для заявки #{booking_id}",
                booking_id=booking_id,
            )
        )
        session.commit()
        logging.info(
            f"Оплата для заявки #{booking.id} отклонена, статус возвращён в awaiting_payment, новый payment_deadline: {booking.payment_deadline}"
        )
    except ValueError:
        await callback.answer("Неверный формат данных заявки.", show_alert=True)
    except Exception as e:
        logging.error(
            f"Ошибка в payment_failed_callback: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer(f"Произошла ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data == "admin_download_log")
async def download_log(callback: CallbackQuery, state: FSMContext):
    from config import ADMIN_CHAT_ID

    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        logging.warning(
            f"Неавторизованная попытка скачивания лога пользователем {callback.from_user.id}"
        )
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    await state.update_data(previous_menu="admin")

    # Получаем текущий год и статус сезона для возврата в админ-панель
    booking_year = session.query(BookingYear).first()
    current_year = booking_year.year if booking_year else 2025
    season = session.query(BookingSeason).first()
    season_status = "Активен" if season and season.is_active else "Завершён"

    try:
        log_file = str(LOG_PATH)
        if not os.path.exists(log_file):
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                ]
            )
            await callback.message.edit_text(
                "❗ Файл логов не найден.", reply_markup=kb
            )
            logging.error(f"Файл логов {log_file} не найден")
            await callback.answer()
            return

        # Отправляем файл логов
        file = FSInputFile(
            log_file, filename=f"bot_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        )
        await callback.message.answer_document(
            document=file, caption="📄 Файл логов bot.log"
        )

        # Очищаем файл логов
        try:
            # Закрываем текущий обработчик логов
            for handler in logging.getLogger().handlers[:]:
                if isinstance(handler, RotatingFileHandler):
                    handler.stream.close()
                    logging.getLogger().removeHandler(handler)

            # Очищаем файл
            with open(log_file, "w", encoding="utf-8") as f:
                f.write("")

            # Восстанавливаем обработчик логов
            new_handler = RotatingFileHandler(
                filename=log_file,
                maxBytes=10 * 1024 * 1024,  # 10 МБ
                backupCount=5,
                encoding="utf-8",
            )
            new_handler.setFormatter(
                logging.Formatter(
                    "%(asctime)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            logging.getLogger().addHandler(new_handler)
            logging.info(
                f"Файл логов {log_file} очищен после скачивания администратором {callback.from_user.id}"
            )
        except Exception as clear_error:
            logging.error(
                f"Ошибка при очистке файла логов {log_file}: {str(clear_error)}\n{traceback.format_exc()}"
            )
            await callback.message.answer(
                f"⚠️ Лог отправлен, но не удалось очистить файл логов: {str(clear_error)}"
            )

        # Формируем админ-панель для возврата
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📋 Активные заявки", callback_data="admin_view_bookings"
                    )
                ],
            [
                InlineKeyboardButton(
                    text="\U0001f4ac \u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u044f \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0438", callback_data="admin_support_tickets"
                )
            ],
                [
                    InlineKeyboardButton(
                        text="📅 Календарь бронирований",
                        callback_data="admin_view_calendar",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📊 Статистика за год",
                        callback_data="admin_select_stats_year",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📈 Изменить цены", callback_data="admin_change_prices"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⭐ Управление отзывами",
                        callback_data="admin_manage_reviews",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📰 Создать новость", callback_data="admin_create_news"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💾 Скачать базу данных", callback_data="admin_download_db"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📄 Скачать лог", callback_data="admin_download_log"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"📅 Изменить год бронирования ({current_year})",
                        callback_data="admin_change_booking_year",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"🌞 Управление сезоном ({season_status})",
                        callback_data="admin_manage_season",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👥 Пользователи с согласием",
                        callback_data="admin_view_users",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
            ]
        )
        new_text = "👨‍💼 Панель администратора:"

        # Проверяем, нужно ли обновлять сообщение
        current_text = callback.message.text or ""
        current_reply_markup = callback.message.reply_markup
        if current_text != new_text or current_reply_markup != kb:
            try:
                await callback.message.edit_text(new_text, reply_markup=kb)
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    logging.debug(
                        f"Сообщение админ-панели не обновлено: текст и клавиатура не изменились"
                    )
                elif "query is too old" in str(e):
                    await callback.message.answer(new_text, reply_markup=kb)
                    logging.info(
                        f"Отправлено новое сообщение админ-панели из-за устаревшего query"
                    )
                else:
                    raise
        else:
            logging.debug(
                f"Сообщение админ-панели не обновлено: текст и клавиатура идентичны"
            )

        session.add(
            AdminLog(
                admin_id=callback.from_user.id, action="Скачал и очистил файл логов"
            )
        )
        session.commit()
        logging.info(f"Админ {callback.from_user.id} скачал и очистил файл логов")
    except Exception as e:
        # Восстанавливаем обработчик логов в случае ошибки
        for handler in logging.getLogger().handlers[:]:
            if isinstance(handler, RotatingFileHandler):
                handler.stream.close()
                logging.getLogger().removeHandler(handler)
        new_handler = RotatingFileHandler(
            filename=log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        new_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
            )
        )
        logging.getLogger().addHandler(new_handler)
        logging.error(f"Ошибка в download_log: {str(e)}\n{traceback.format_exc()}")

        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
            ]
        )
        await callback.message.edit_text(
            f"❗ Ошибка при скачивании файла логов: {str(e)}", reply_markup=kb
        )
    await callback.answer()


@router.callback_query(F.data.startswith("cancel_booking_"))
async def cancel_user_booking(callback: CallbackQuery, state: FSMContext):
    booking_id = int(callback.data.split("_")[2])
    await state.update_data(booking_id=booking_id)
    await callback.message.answer("Укажите причину отмены заявки:")
    await state.set_state(BookingStates.waiting_for_cancellation_reason)


@router.message(BookingStates.waiting_for_cancellation_reason)
async def process_cancellation_reason(message: Message, state: FSMContext):
    data = await state.get_data()
    booking_id = data["booking_id"]
    booking = session.query(Booking).filter_by(id=booking_id).first()
    if booking:
        booking.status = BookingStatus.AWAITING_CANCELLATION.value
        booking.comment = f"Причина отмены: {message.text}\n{booking.comment}"
        session.commit()
        children_beds = get_children_beds(booking)
        total_people = booking.adults + sum(children_beds)
        total = await calculate_revenue(
            booking.room_type, total_people, booking.date_from, booking.date_to
        )
        try:
            await message.bot.send_message(
                ADMIN_CHAT_ID,
                f"📌 Запрос на отмену заявки #{booking.id}\n"
                f"👤 {booking.full_name} (@{booking.username or 'без ника'})\n"
                f"📞 {booking.phone}\n"
                f"🏠 {booking.room_type.replace('+', ' + ')}-местный\n"
                f"📅 Заезд: {booking.date_from.strftime('%d.%m.%Y')} после 14:00\n"
                f"📅 Выезд: {booking.date_to.strftime('%d.%m.%Y')} до 12:00\n"
                f"👨‍👩‍👧‍👦 Взрослых: {booking.adults}, Детей: {booking.children}\n"
                f"💰 Сумма: {total}₽\n"
                f"💬 Причина: {message.text}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Подтвердить отмену",
                                callback_data=f"admin_confirm_cancel_{booking.id}",
                            )
                        ],
                        [
                            InlineKeyboardButton(
                                text="❌ Отклонить отмену",
                                callback_data=f"admin_reject_cancel_{booking.id}",
                            )
                        ],
                    ]
                ),
            )
            await message.answer("Запрос на отмену отправлен администратору.")
        except Exception as e:
            logging.error(
                f"Ошибка в process_cancellation_reason для booking_id {booking_id}: {str(e)}"
            )
            await message.answer(
                "❗ Ошибка при отправке запроса на отмену. Попробуйте снова."
            )
    await state.clear()


@router.callback_query(F.data == "view_reviews")
async def view_reviews(callback: CallbackQuery, state: FSMContext):
    reviews = session.query(Review).order_by(Review.created_at.desc()).all()

    # Сохраняем предыдущее состояние для кнопки "Назад"
    await state.update_data(previous_menu="main_menu")

    if not reviews:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
            ]
        )
        try:
            await callback.message.edit_text("⭐ Отзывов пока нет.", reply_markup=kb)
        except TelegramBadRequest as e:
            if "there is no text in the message to edit" in str(e):
                await callback.message.delete()
                await callback.message.answer("⭐ Отзывов пока нет.", reply_markup=kb)
            else:
                raise
        logging.info(f"Пользователь {callback.from_user.id} просмотрел отзывы: count=0")
        await callback.answer()
        return

    # Подсчёт среднего рейтинга
    total_rating = sum(review.rating for review in reviews)
    review_count = len(reviews)
    average_rating = total_rating / review_count if review_count > 0 else 0
    stars = "★" * int(round(average_rating)) + "☆" * (5 - int(round(average_rating)))

    text = f"⭐ *Средний рейтинг*: {average_rating:.1f} {stars}\n"
    text += f"📋 *Всего отзывов*: {review_count}\n\n"
    text += "📌 *Последние отзывы*:\n"

    for review in reviews[:5]:
        booking = session.query(Booking).filter_by(id=review.booking_id).first()
        text += (
            f"📌 Заявка #{review.booking_id}\n"
            f"👤 Аноним\n"
            f"🏠 {room_type_names[booking.room_type]}\n"
            f"⭐ Рейтинг: {'★' * review.rating}{'☆' * (5 - review.rating)}\n"
            f"💬 {review.comment}\n"
            f"📅 {review.created_at}\n\n"
        )

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
        ]
    )
    try:
        await callback.message.edit_text(text, parse_mode="Markdown", reply_markup=kb)
    except TelegramBadRequest as e:
        if "there is no text in the message to edit" in str(e):
            await callback.message.delete()
            await callback.message.answer(text, parse_mode="Markdown", reply_markup=kb)
        else:
            raise
    logging.info(
        f"Пользователь {callback.from_user.id} просмотрел отзывы: count={review_count}, average_rating={average_rating:.1f}"
    )
    await callback.answer()


@router.callback_query(F.data.startswith("leave_review_"))
async def leave_review(callback: CallbackQuery, state: FSMContext):
    booking_id = int(callback.data.split("_")[2])
    await state.update_data(booking_id=booking_id)

    def escape_markdown(text: str) -> str:
        """Экранирование специальных символов для MarkdownV2."""
        if not text:
            return ""
        chars = [
            "_",
            "*",
            "[",
            "]",
            "(",
            ")",
            "~",
            "`",
            ">",
            "#",
            "+",
            "-",
            "=",
            "|",
            "{",
            "}",
            ".",
            "!",
        ]
        for char in chars:
            text = text.replace(char, f"\\{char}")
        return text

    text = (
        f"⭐ *Оставьте отзыв о заявке* `#{booking_id}`:\n\n"
        f"📝 *Формат*:\n"
        f"Рейтинг \\(1–5\\)\n"
        f"Комментарий\n\n"
        f"📌 *Пример*:\n"
        f"```5\n"
        f"Отличный номер, всё понравилось!```"
    )
    await callback.message.answer(
        text,
        parse_mode="MarkdownV2",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")]
            ]
        ),
    )
    await state.set_state(BookingStates.waiting_for_review)
    logging.info(
        f"Пользователь {callback.from_user.id} начал оставлять отзыв для заявки #{booking_id}"
    )


@router.message(BookingStates.waiting_for_review)
async def save_review(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        booking_id = data["booking_id"]
        booking = session.query(Booking).filter_by(id=booking_id).first()
        if not booking:
            await message.answer("Заявка не найдена.")
            await state.clear()
            return
        lines = message.text.strip().split("\n", 1)
        rating = int(lines[0])
        comment = lines[1] if len(lines) > 1 else "Без комментария"
        if not 1 <= rating <= 5:
            await message.answer("Рейтинг должен быть от 1 до 5.")
            return
        review = Review(
            booking_id=booking_id,
            user_id=message.from_user.id,
            rating=rating,
            comment=comment,
        )
        session.add(review)
        session.commit()
        await message.answer("Спасибо за ваш отзыв! 😊")
        try:
            await message.bot.send_message(
                ADMIN_CHAT_ID,
                f"⭐ Новый отзыв к заявке #{booking_id}\n"
                f"👤 @{booking.username or 'Аноним'}\n"
                f"🏠 {booking.room_type.replace('+', ' + ')}-местный\n"
                f"⭐ Рейтинг: {'★' * rating}{'☆' * (5 - rating)}\n"
                f"💬 {comment}",
            )
        except Exception as e:
            logging.error(
                f"Ошибка при уведомлении администратора о новом отзыве для booking_id {booking_id}: {str(e)}"
            )
        await state.clear()
    except (ValueError, IndexError):
        await message.answer("Неверный формат. Пример:\n5\nОтличный номер!")
    except Exception as e:
        logging.error(f"Ошибка в save_review для booking_id {booking_id}: {str(e)}")
        await message.answer(f"Ошибка: {str(e)}")


@router.callback_query(F.data == "support")
async def start_support(callback: CallbackQuery, state: FSMContext):
    await state.update_data(
        support_user_id=callback.from_user.id, support_context="general"
    )
    await send_reply_keyboard(callback.message, compact_main_keyboard())
    await callback.message.answer(
        f"📞 Напишите ваш вопрос или проблему, и администратор ответит вам.\n"
        f"Либо свяжитесь напрямую: {ADMIN_USERNAME}"
    )
    await state.set_state(BookingStates.waiting_for_support_message)
    logging.debug(
        f"Пользователь {callback.from_user.id} начал общее обращение в поддержку"
    )


@router.message(
    F.text.in_(
        {
            BTN_BOOK,
            BTN_AVAILABILITY,
            BTN_MY_BOOKINGS,
            BTN_REVIEWS,
            BTN_SUPPORT,
            BTN_ABOUT,
            BTN_ADMIN,
            BTN_ADMIN_BOOKINGS,
            BTN_ADMIN_CALENDAR,
            BTN_ADMIN_CREATE_BOOKING,
            BTN_ADMIN_DB,
            BTN_ADMIN_LOG,
            BTN_ADMIN_NEWS,
            BTN_ADMIN_PAYMENT,
            BTN_ADMIN_PRICES,
            BTN_ADMIN_REVIEWS,
            BTN_ADMIN_SEASON,
            BTN_ADMIN_STATS,
            BTN_ADMIN_SUPPORT,
            BTN_ADMIN_USERS,
            BTN_ADMIN_YEAR,
            BTN_MAIN_MENU,
            BTN_BACK,
            BTN_RULES_LIVING,
            BTN_TERRITORY,
            BTN_BOOKING_RULES,
            BTN_CANCELLATION_RULES,
            BTN_ROOMS_DESCRIPTION,
            BTN_FAQ,
            BTN_PREV_PHOTO,
            BTN_NEXT_PHOTO,
            BTN_PREV_ROOM,
            BTN_NEXT_ROOM,
            "🔎 Проверить свободные даты",
        }
    )
)
async def handle_reply_keyboard_menu(message: Message, state: FSMContext):
    text = message.text.strip()
    if text in {BTN_MAIN_MENU, BTN_BACK}:
        await show_main_menu_message(message, state)
        return

    fake_callback = MessageBackedCallback(message)
    if text == BTN_BOOK:
        await process_booking(fake_callback, state)
    elif text in {BTN_AVAILABILITY, "🔎 Проверить свободные даты"}:
        fake_callback.data = "check_availability"
        await start_availability_check(fake_callback, state)
    elif text == BTN_MY_BOOKINGS:
        fake_callback.data = "my_bookings"
        await show_my_bookings(fake_callback, state)
    elif text == BTN_REVIEWS:
        fake_callback.data = "view_reviews"
        await view_reviews(fake_callback, state)
    elif text == BTN_SUPPORT:
        fake_callback.data = "support"
        await start_support(fake_callback, state)
    elif text == BTN_ABOUT:
        fake_callback.data = "about_us"
        await show_about_us(fake_callback, state)
    elif text == BTN_RULES_LIVING:
        fake_callback.data = "rules_living"
        await show_rules_living(fake_callback, state)
    elif text == BTN_TERRITORY:
        fake_callback.data = "territory_overview"
        await show_territory_overview(fake_callback, state, message.bot)
    elif text == BTN_BOOKING_RULES:
        fake_callback.data = "booking_rules"
        await show_booking_rules(fake_callback, state)
    elif text == BTN_CANCELLATION_RULES:
        fake_callback.data = "cancellation_rules"
        await show_cancellation_rules(fake_callback, state)
    elif text == BTN_ROOMS_DESCRIPTION:
        fake_callback.data = "rooms_description"
        await show_rooms_description(fake_callback, state, message.bot)
    elif text == BTN_FAQ:
        fake_callback.data = "faq"
        await show_faq(fake_callback, state)
    elif text == BTN_PREV_PHOTO:
        fake_callback.data = "prev_photo"
        await navigate_photos(fake_callback, state, message.bot)
    elif text == BTN_NEXT_PHOTO:
        fake_callback.data = "next_photo"
        await navigate_photos(fake_callback, state, message.bot)
    elif text == BTN_PREV_ROOM:
        fake_callback.data = "prev_room"
        await navigate_rooms(fake_callback, state, message.bot)
    elif text == BTN_NEXT_ROOM:
        fake_callback.data = "next_room"
        await navigate_rooms(fake_callback, state, message.bot)
    elif text == BTN_ADMIN:
        if str(message.from_user.id) != str(ADMIN_CHAT_ID):
            await message.answer("У вас нет доступа к админ-панели.")
            return
        await admin_panel(message, state)
    elif text.startswith("📋 Активные заявки"):
        fake_callback.data = "admin_view_bookings"
        await list_bookings(fake_callback, state)
    elif text == BTN_ADMIN_SUPPORT:
        fake_callback.data = "admin_support_tickets"
        await admin_support_tickets(fake_callback, state)
    elif text == BTN_ADMIN_CALENDAR:
        fake_callback.data = "admin_view_calendar"
        await admin_view_calendar(fake_callback, state)
    elif text == BTN_ADMIN_STATS:
        fake_callback.data = "admin_select_stats_year"
        await admin_select_stats_year(fake_callback, state)
    elif text == BTN_ADMIN_REVIEWS:
        fake_callback.data = "admin_manage_reviews"
        await admin_manage_reviews(fake_callback)
    elif text == BTN_ADMIN_DB:
        fake_callback.data = "admin_download_db"
        await download_database(fake_callback, state)
    elif text == BTN_ADMIN_LOG:
        fake_callback.data = "admin_download_log"
        await download_log(fake_callback, state)
    elif text == BTN_ADMIN_YEAR:
        fake_callback.data = "admin_change_booking_year"
        await admin_change_booking_year(fake_callback, state)
    elif text == BTN_ADMIN_SEASON:
        fake_callback.data = "admin_manage_season"
        await admin_manage_season(fake_callback, state)
    elif text == BTN_ADMIN_USERS:
        fake_callback.data = "admin_view_users"
        await admin_view_users(fake_callback, state)
    elif text == BTN_ADMIN_PRICES:
        from bot.routers.admin_prices import admin_change_prices

        fake_callback.data = "admin_change_prices"
        await admin_change_prices(fake_callback, state)
    elif text == BTN_ADMIN_NEWS:
        from bot.routers.admin_news import admin_create_news

        fake_callback.data = "admin_create_news"
        await admin_create_news(fake_callback, state)
    elif text == BTN_ADMIN_CREATE_BOOKING:
        await start_admin_create_booking(message, state)
    elif text == BTN_ADMIN_PAYMENT:
        await start_admin_payment_update(message, state)


@router.message(F.text.startswith("/reject_"))
async def reject_booking_command(message: Message):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        await message.answer("У вас нет прав на это действие.")
        return
    try:
        booking_id = int(message.text.split("_")[1])
        booking = session.query(Booking).filter_by(id=booking_id).first()
        if booking:
            booking.status = BookingStatus.REJECTED.value
            session.commit()
            try:
                if booking.user_id:
                    await message.bot.send_message(
                        chat_id=booking.user_id,
                        text=(
                            f"❌ Ваша заявка #{booking.id} отклонена администратором.\n"
                            f"Для уточнения деталей свяжитесь с {ADMIN_USERNAME}"
                        ),
                    )
            except Exception as e:
                await message.answer(f"❗ Ошибка при уведомлении пользователя: {e}")
            await message.answer(f"Заявка #{booking.id} отклонена ❌")
            session.add(
                AdminLog(
                    admin_id=message.from_user.id,
                    action=f"Отклонил заявку #{booking_id}",
                    booking_id=booking_id,
                )
            )
            session.commit()
    except (IndexError, ValueError):
        await message.answer("Неверный формат команды. Пример: /reject_123")


@router.message(F.text.startswith("/reject_cancel_"))
async def reject_cancel_command(message: Message):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        await message.answer("У вас нет прав на это действие.")
        return
    try:
        booking_id = int(message.text.split("_")[2])
        booking = session.query(Booking).filter_by(id=booking_id).first()
        if booking:
            booking.status = BookingStatus.PENDING.value
            session.commit()
            try:
                if booking.user_id:
                    await message.bot.send_message(
                        chat_id=booking.user_id,
                        text=(
                            f"❌ Отмена заявки #{booking.id} отклонена администратором.\n"
                            f"Для уточнения деталей свяжитесь с {ADMIN_USERNAME}"
                        ),
                    )
            except Exception as e:
                await message.answer(f"❗ Ошибка при уведомлении пользователя: {e}")
            await message.answer(f"Отмена заявки #{booking.id} отклонена ❌")
            session.add(
                AdminLog(
                    admin_id=message.from_user.id,
                    action=f"Отклонил отмену заявки #{booking_id}",
                    booking_id=booking_id,
                )
            )
            session.commit()
    except (IndexError, ValueError):
        await message.answer("Неверный формат команды. Пример: /reject_cancel_123")


@router.message(F.text, StateFilter(None))
async def recover_admin_payment_amount_from_reply(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        await handle_text_out_of_context(message, state)
        return

    reply = message.reply_to_message
    prompt_text = ((reply.text or reply.caption) if reply else "") or ""
    payment_update_match = re.search(r"Заявка\s+#(\d+)", prompt_text, re.IGNORECASE)
    if payment_update_match and "новую общую внесённую сумму" in prompt_text.lower():
        booking_id = int(payment_update_match.group(1))
        logging.warning(
            "Recovered lost admin payment update state for booking #%s",
            booking_id,
        )
        await state.update_data(admin_payment_booking_id=booking_id)
        await state.set_state(BookingStates.waiting_for_admin_payment_update)
        await process_admin_payment_update(message, state)
        return

    match = re.search(r"Чек заявки\s+#(\d+)", prompt_text, re.IGNORECASE)
    if not match or "фактически поступила" not in prompt_text.lower():
        await handle_text_out_of_context(message, state)
        return

    booking_id = int(match.group(1))
    logging.warning(
        "Recovered lost admin payment amount state for booking #%s",
        booking_id,
    )
    await state.update_data(admin_confirm_payment_booking_id=booking_id)
    await state.set_state(BookingStates.waiting_for_admin_payment_confirmation_amount)
    await process_admin_payment_confirmation_amount(message, state, message.bot)


@router.message(F.text, StateFilter(None))
async def handle_text_out_of_context(message: Message, state: FSMContext):
    user_id = message.from_user.id
    current_state = await state.get_state()
    logging.debug(
        f"handle_text_out_of_context вызван для пользователя {user_id}, текущее состояние: {current_state}"
    )

    # Пропускаем состояния, где текст ожидаем
    expected_text_states = [
        BookingStates.waiting_for_duration,
        BookingStates.waiting_for_adults,
        BookingStates.waiting_for_children,
        BookingStates.waiting_for_comment_text,
        BookingStates.waiting_for_manual_contact,
        BookingStates.waiting_for_cancellation_reason,
        BookingStates.waiting_for_support_message,
        BookingStates.waiting_for_admin_response,
        BookingStates.waiting_for_price_change_dates,
        BookingStates.waiting_for_price_change_value,
        BookingStates.waiting_for_review,
    ]
    if current_state in expected_text_states:
        logging.debug(
            f"Текст отправлен в состоянии {current_state}, обработка передана соответствующему обработчику"
        )
        return

    # Показываем главное меню
    buttons = [
        [InlineKeyboardButton(text="📅 Забронировать номер", callback_data="book")],
        [InlineKeyboardButton(text="🔎 Проверить свободные даты", callback_data="check_availability")],
        [InlineKeyboardButton(text="📋 Мои заявки", callback_data="my_bookings")],
        [
            InlineKeyboardButton(
                text="⭐ Посмотреть отзывы", callback_data="view_reviews"
            )
        ],
        [InlineKeyboardButton(text="📞 Поддержка", callback_data="support")],
        [InlineKeyboardButton(text="ℹ️ О нас", callback_data="about_us")],
    ]
    if str(user_id) == str(ADMIN_CHAT_ID):
        buttons.append(
            [InlineKeyboardButton(text="👨‍💼 Админ-панель", callback_data="admin")]
        )
        message_text = "📌 Вы администратор. Используйте меню ниже для управления заявками или выберите 'Админ-панель'."
    else:
        message_text = "📌 Пожалуйста, используйте меню для взаимодействия с ботом."
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(message_text, reply_markup=kb)
    logging.info(
        f"Пользователь {user_id} отправил текст вне контекста (состояние: {current_state}), предложено меню"
    )


@router.message(F.photo, StateFilter(None))
async def recover_payment_photo_from_reply(message: Message, state: FSMContext):
    reply = message.reply_to_message
    prompt_text = ((reply.text or reply.caption) if reply else "") or ""
    match = re.search(r"заявки\s+#(\d+)", prompt_text, re.IGNORECASE)
    if not match or "фото чека" not in prompt_text.lower():
        await handle_non_text_out_of_context(message, state)
        return

    booking_id = int(match.group(1))
    booking = (
        session.query(Booking)
        .filter_by(
            id=booking_id,
            user_id=message.from_user.id,
            status=BookingStatus.AWAITING_PAYMENT.value,
        )
        .first()
    )
    if not booking:
        await message.answer(
            f"❌ Заявка #{booking_id} не найдена или уже не ожидает оплату."
        )
        return

    logging.warning(
        "Recovered lost payment screenshot state for booking #%s, user %s",
        booking_id,
        message.from_user.id,
    )
    await state.update_data(booking_id=booking_id)
    await state.set_state(BookingStates.waiting_for_payment_screenshot)
    await handle_payment_photo(message, state)


@router.message(~F.text, StateFilter(None))
async def handle_non_text_out_of_context(message: Message, state: FSMContext):
    user_id = message.from_user.id
    current_state = await state.get_state()
    content_type = message.content_type
    logging.debug(
        f"handle_non_text_out_of_context вызван для пользователя {user_id}, тип контента: {content_type}, состояние: {current_state}"
    )

    # Пропускаем состояния, где контент ожидаем
    expected_content_states = [
        BookingStates.waiting_for_payment_screenshot,  # Для фото оплаты
        BookingStates.waiting_for_contact,  # Для контакта
        BookingStates.waiting_for_review,  # Для отзывов
    ]
    if current_state in expected_content_states:
        logging.debug(
            f"Контент ({content_type}) отправлен в состоянии {current_state}, обработка передана соответствующему обработчику"
        )
        return

    # Показываем главное меню
    buttons = [
        [InlineKeyboardButton(text="📅 Забронировать номер", callback_data="book")],
        [InlineKeyboardButton(text="🔎 Проверить свободные даты", callback_data="check_availability")],
        [InlineKeyboardButton(text="📋 Мои заявки", callback_data="my_bookings")],
        [
            InlineKeyboardButton(
                text="⭐ Посмотреть отзывы", callback_data="view_reviews"
            )
        ],
        [InlineKeyboardButton(text="📞 Поддержка", callback_data="support")],
        [InlineKeyboardButton(text="ℹ️ О нас", callback_data="about_us")],
    ]
    if str(user_id) == str(ADMIN_CHAT_ID):
        buttons.append(
            [InlineKeyboardButton(text="👨‍💼 Админ-панель", callback_data="admin")]
        )
        message_text = "📌 Вы администратор. Используйте меню ниже для управления заявками или выберите 'Админ-панель'."
    else:
        message_text = "📌 Пожалуйста, используйте меню для взаимодействия с ботом."
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.answer(message_text, reply_markup=kb)
    logging.info(
        f"Пользователь {user_id} отправил нетекстовое сообщение ({content_type}) вне контекста (состояние: {current_state}), предложено меню"
    )


@router.callback_query(F.data.startswith("start_payment_support_"))
async def start_payment_support(callback: CallbackQuery, state: FSMContext):
    booking_id = int(callback.data.split("_")[3])
    booking = session.query(Booking).filter_by(id=booking_id).first()
    if not booking or booking.user_id != callback.from_user.id:
        await callback.answer("У вас нет доступа к этой заявке.", show_alert=True)
        return
    await state.update_data(
        support_user_id=callback.from_user.id,
        booking_id=booking_id,
        support_context="payment",
    )
    await callback.message.answer(
        f"📞 Напишите ваш вопрос или проблему по оплате заявки #{booking_id}, и администратор ответит вам.\n"
        f"Либо свяжитесь напрямую: {ADMIN_USERNAME}"
    )
    await state.set_state(BookingStates.waiting_for_support_message)
    logging.debug(
        f"Пользователь {callback.from_user.id} начал обращение в поддержку по оплате заявки #{booking_id}"
    )


@router.message(BookingStates.waiting_for_support_message)
async def forward_support_message(message: Message, state: FSMContext):
    data = await state.get_data()
    support_user_id = data.get("support_user_id", message.from_user.id)
    booking_id = data.get("booking_id")
    support_context = data.get("support_context", "general")
    username = message.from_user.username
    username_text = f"@{username}" if username else "без ника"
    booking = (
        session.query(Booking).filter_by(id=booking_id).first() if booking_id else None
    )

    # Определяем заголовок по контексту
    context_headers = {
        "general": "📞 Общее обращение в поддержку",
        "payment": f"💸 Вопрос по оплате заявки #{booking_id}",
        "booking": f"📌 Вопрос по заявке #{booking_id}",
        "reply": f"💬 Ответ на сообщение по заявке #{booking_id or 'без ID'}",
    }
    header = context_headers.get(support_context, "📞 Сообщение в поддержку")

    try:
        if booking:
            context = (
                f"👤 {booking.full_name}\n"
                f"🆔 ID: {support_user_id}\n"
                f"🔗 Ник: {username_text}\n"
                f"📞 {booking.phone}\n"
                f"🏠 {booking.room_type.replace('+', ' + ')}-местный\n"
                f"📅 Заезд: {booking.date_from.strftime('%d.%m.%Y')} после 14:00\n"
                f"📅 Выезд: {booking.date_to.strftime('%d.%m.%Y')} до 12:00"
            )
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💬 Ответить",
                            callback_data=f"support_reply_{support_user_id}_{booking_id}",
                        )
                    ]
                ]
            )
            await message.bot.send_message(
                ADMIN_CHAT_ID,
                f"{header}:\n{context}\n\n{message.text}",
                reply_markup=kb,
            )
            is_payment_support = (
                support_context == "payment"
                and booking.status
                in {
                    BookingStatus.AWAITING_PAYMENT.value,
                    "awaiting_payment_confirmation",
                }
            )
            kb_user = (
                InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="📸 Отправить скриншот",
                                callback_data=f"send_screenshot_{booking.id}",
                            )
                        ]
                    ]
                )
                if is_payment_support
                else InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ В меню", callback_data="back_to_main"
                            )
                        ]
                    ]
                )
            )
            await message.answer(
                f"Ваше сообщение отправлено. Дождитесь ответа администратора{' или отправьте скриншот оплаты' if is_payment_support else ''}.",
                reply_markup=kb_user,
            )
            logging.info(
                f"Пользователь {support_user_id} отправил сообщение в поддержку по заявке #{booking_id} (контекст: {support_context})"
            )
        else:
            context = f"Пользователь: {username_text}\nID: {support_user_id}"
            await message.bot.send_message(
                ADMIN_CHAT_ID,
                f"{header}:\n{context}\n\n{message.text}",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="💬 Ответить",
                                callback_data=f"support_reply_{support_user_id}_",
                            )
                        ]
                    ]
                ),
            )
            await message.answer(
                "Ваше сообщение отправлено. Дождитесь ответа администратора.",
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="⬅️ В меню", callback_data="back_to_main"
                            )
                        ]
                    ]
                ),
            )
            logging.info(
                f"Пользователь {support_user_id} отправил сообщение в поддержку без заявки (контекст: {support_context})"
            )
        # Сохраняем состояние для продолжения переписки
        await state.update_data(
            support_user_id=support_user_id,
            booking_id=booking_id,
            support_context=support_context,
        )
        await state.set_state(BookingStates.waiting_for_support_message)
        # Сохраняем сообщение в базе
        session.add(
            SupportMessage(
                user_id=support_user_id,
                booking_id=booking_id,
                message=message.text,
                from_admin=False,
                timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            )
        )
        session.commit()
    except Exception as e:
        logging.error(
            f"Ошибка в forward_support_message для user_id {support_user_id}, context={support_context}: {str(e)}"
        )
        await message.answer("❗ Ошибка при отправке сообщения. Попробуйте снова.")


@router.callback_query(F.data.regexp(r"admin_paid_(\d+)"))
async def mark_paid_callback(callback: CallbackQuery, bot: Bot, state: FSMContext):
    try:
        if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
            await callback.answer("У вас нет прав на это действие", show_alert=True)
            return
        booking_id = int(callback.data.split("_")[2])
        booking = session.query(Booking).filter_by(id=booking_id).first()
        if not booking:
            await callback.answer(f"Заявка #{booking_id} не найдена.", show_alert=True)
            return
        await callback.message.edit_reply_markup(reply_markup=None)
        await state.update_data(admin_confirm_payment_booking_id=booking.id)
        await state.set_state(BookingStates.waiting_for_admin_payment_confirmation_amount)
        await callback.message.answer(
            f"💳 Чек заявки #{booking.id}\n\n"
            "Введите сумму, которая фактически поступила.\n"
            "Например: 10000",
            reply_markup=ForceReply(
                selective=True,
                input_field_placeholder=f"Сумма оплаты заявки #{booking.id}",
            ),
        )
        await callback.answer()
    except ValueError:
        logging.error(
            f"Ошибка в mark_paid_callback: Неверный формат booking_id в callback.data={callback.data}"
        )
        await callback.answer("Неверный формат данных заявки.", show_alert=True)
    except Exception as e:
        logging.error(
            f"Ошибка в mark_paid_callback: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer(f"Произошла ошибка: {str(e)}", show_alert=True)


@router.message(BookingStates.waiting_for_admin_payment_confirmation_amount, F.text)
async def process_admin_payment_confirmation_amount(
    message: Message, state: FSMContext, bot: Bot
):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    data = await state.get_data()
    booking_id = data.get("admin_confirm_payment_booking_id")
    numbers = re.findall(r"\d+", message.text or "")
    if not booking_id or not numbers or int(numbers[0]) <= 0:
        await message.answer("Введите полученную сумму числом. Например: 10000")
        return

    booking = session.query(Booking).filter_by(id=int(booking_id)).first()
    if not booking:
        await state.clear()
        await message.answer(f"Заявка #{booking_id} не найдена.")
        return

    paid = int(numbers[0])
    total_people = booking.adults + sum(get_children_beds(booking))
    calculated_total = await calculate_revenue(
        booking.room_type, total_people, booking.date_from, booking.date_to
    )
    balance = apply_confirmed_payment(booking, paid, calculated_total)
    session.add(
        AdminLog(
            admin_id=message.from_user.id,
            action=f"Подтвердил оплату заявки #{booking.id}: внесено {paid}₽",
            booking_id=booking.id,
        )
    )
    session.commit()
    session.expire_all()
    clear_booked_dates_cache()
    await state.clear()

    remaining_text = (
        f"\n🧾 Осталось оплатить: {balance['remaining']}₽"
        if balance["remaining"] > 0
        else "\n✅ Оплачено полностью"
    )
    if booking.user_id:
        await bot.send_message(
            booking.user_id,
            (
                f"✅ Оплата по заявке #{booking.id} подтверждена!\n"
                f"💰 Общая сумма: {balance['total']}₽\n"
                f"✅ Внесено: {balance['paid']}₽"
                f"{remaining_text}\n\n"
                f"Ждём вас {booking.date_from.strftime('%d.%m.%Y')} после 14:00."
            ),
        )
    await message.answer(
        f"✅ Оплата заявки #{booking.id} подтверждена\n\n"
        f"💰 Общая сумма: {balance['total']}₽\n"
        f"✅ Внесено: {balance['paid']}₽"
        f"{remaining_text}",
        reply_markup=admin_menu_keyboard(),
    )


@router.callback_query(F.data.regexp(r"admin_confirm_cancel_(\d+)"))
async def confirm_cancel_callback(callback: CallbackQuery):
    try:
        if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
            await callback.answer("У вас нет прав на это действие", show_alert=True)
            return
        booking_id = int(callback.data.split("_")[3])
        booking = session.query(Booking).filter_by(id=booking_id).first()
        if not booking:
            await callback.answer(f"Заявка #{booking_id} не найдена.", show_alert=True)
            return
        children_beds = get_children_beds(booking)
        children_needing_beds = sum(children_beds)
        total_people = booking.adults + children_needing_beds
        total_price = await calculate_revenue(
            booking.room_type, total_people, booking.date_from, booking.date_to
        )
        booking.status = BookingStatus.CANCELED.value
        session.commit()
        clear_booked_dates_cache()
        try:
            if booking.user_id:
                await callback.message.bot.send_message(
                    chat_id=booking.user_id,
                    text=f"✅ Отмена заявки #{booking.id} подтверждена.",
                )
        except Exception as e:
            await callback.message.answer(
                f"❗ Ошибка при уведомлении пользователя: {e}"
            )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"Отмена заявки #{booking.id} подтверждена ✅")
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Подтвердил отмену заявки #{booking_id}, сумма {total_price}₽ исключена из статистики",
                booking_id=booking_id,
            )
        )
        session.commit()
        logging.info(
            f"Админ {callback.from_user.id} подтвердил отмену заявки #{booking.id}, сумма {total_price}₽ исключена из статистики"
        )
        await callback.answer()
    except ValueError:
        await callback.answer("Неверный формат данных заявки.", show_alert=True)
    except Exception as e:
        logging.error(
            f"Ошибка в confirm_cancel_callback: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer(f"Произошла ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data.regexp(r"admin_reject_cancel_(\d+)"))
async def reject_cancel_callback(callback: CallbackQuery):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    try:
        booking_id = int(callback.data.split("_")[3])
        booking = session.query(Booking).filter_by(id=booking_id).first()
        if not booking:
            await callback.answer(f"Заявка #{booking_id} не найдена.", show_alert=True)
            return
        booking.status = BookingStatus.PENDING.value
        session.commit()
        try:
            if booking.user_id:
                await callback.message.bot.send_message(
                    chat_id=booking.user_id,
                    text=(
                        f"❌ Отмена заявки #{booking.id} отклонена администратором.\n"
                        f"Для уточнения деталей свяжитесь с {ADMIN_USERNAME}"
                    ),
                )
        except Exception as e:
            await callback.message.answer(
                f"❗ Ошибка при уведомлении пользователя: {e}"
            )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(f"Отмена заявки #{booking.id} отклонена ❌")
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Отклонил отмену заявки #{booking_id}",
                booking_id=booking_id,
            )
        )
        session.commit()
    except ValueError:
        await callback.answer("Неверный формат данных заявки.", show_alert=True)
    except Exception as e:
        logging.error(f"Ошибка в reject_cancel_callback: {str(e)}")
        await callback.answer(f"Произошла ошибка: {str(e)}", show_alert=True)


@router.message(Command("cancel_expired"))
async def cancel_expired_bookings(message: Message, bot: Bot):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        await message.answer("У вас нет прав на это действие.")
        return
    now = datetime.now()
    bookings = (
        session.query(Booking)
        .filter(Booking.status == BookingStatus.AWAITING_PAYMENT.value, Booking.payment_deadline <= now)
        .all()
    )
    if not bookings:
        await message.answer("Нет просроченных заявок для отмены.")
        return
    for booking in bookings:
        try:
            booking.status = BookingStatus.CANCELED.value
            session.commit()
            await bot.send_message(
                booking.user_id,
                f"❌ Ваша заявка #{booking.id} была отменена, так как оплата не подтверждена в течение 3 часов.",
            )
            await bot.send_message(
                ADMIN_CHAT_ID,
                f"📌 Заявка #{booking.id} автоматически отменена из-за истечения времени оплаты.",
            )
            logging.info(f"Заявка #{booking.id} отменена через команду /cancel_expired")
        except Exception as e:
            logging.error(f"Ошибка при отмене заявки #{booking.id}: {str(e)}")
    await message.answer(f"Отменено {len(bookings)} просроченных заявок.")


@router.callback_query(F.data == "admin_view_bookings")
async def list_bookings(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    # Сохраняем предыдущее состояние для кнопки "Назад"
    await state.update_data(previous_menu="admin")

    # Синхронизированный словарь типов номеров
    room_counts = {room_type: 0 for room_type in room_type_names}
    # Считаем количество заявок в ожидании оплаты
    awaiting_payment_count = (
        session.query(Booking).filter(Booking.status == BookingStatus.AWAITING_PAYMENT.value).count()
    )

    for booking in (
        session.query(Booking)
        .filter(
            Booking.status.in_(ACTIVE_BOOKING_STATUSES)
        )
        .all()
    ):
        if booking.room_type in room_counts:
            room_counts[booking.room_type] += 1
        else:
            logging.warning(
                f"Неизвестный тип номера в базе: {booking.room_type}, заявка #{booking.id}"
            )

    keyboard_rows = [
        [KeyboardButton(text=f"{room_type_names[room]} ({count})")]
        for room, count in room_counts.items()
    ]
    keyboard_rows.extend(
        [
            [KeyboardButton(text=f"💰 В ожидании оплаты ({awaiting_payment_count})")],
            [KeyboardButton(text="🔎 Поиск"), KeyboardButton(text="✏️ Изменить")],
            [KeyboardButton(text="Новые"), KeyboardButton(text="Оплаченные")],
            [KeyboardButton(text="Отмены")],
            [KeyboardButton(text=BTN_ADMIN), KeyboardButton(text=BTN_BACK)],
        ]
    )
    keyboard = ReplyKeyboardMarkup(keyboard=keyboard_rows, resize_keyboard=True)

    await callback.message.answer(
        "📋 Выберите тип номера или статус для просмотра заявок кнопками снизу:",
        reply_markup=keyboard,
    )
    await state.update_data(admin_booking_room_counts=room_counts)
    await state.set_state(BookingStates.waiting_for_admin_bookings_menu)
    session.add(
        AdminLog(
            admin_id=callback.from_user.id,
            action="Открыл выбор комнат для просмотра заявок",
        )
    )
    session.commit()
    logging.info(
        f"Админ {callback.from_user.id} открыл выбор комнат для просмотра заявок"
    )
    await callback.answer()


@router.message(BookingStates.waiting_for_admin_bookings_menu, F.text)
async def process_admin_bookings_menu(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    text = message.text.strip()
    if text in {BTN_ADMIN, BTN_BACK, BTN_MAIN_MENU}:
        await admin_panel(message, state)
        return
    if text == "🔎 Поиск":
        await message.answer("Введите имя, телефон, username или ID заявки для поиска:")
        await state.set_state(BookingStates.waiting_for_admin_booking_search)
        return
    if text == "✏️ Изменить":
        await message.answer("Введите ID заявки для редактирования:")
        await state.set_state(BookingStates.waiting_for_admin_booking_edit)
        return

    status_map = {
        "Новые": BookingStatus.NEW.value,
        "Оплаченные": BookingStatus.PAID.value,
        "Отмены": BookingStatus.AWAITING_CANCELLATION.value,
    }
    if text in status_map:
        status = status_map[text]
        bookings = (
            session.query(Booking)
            .filter(Booking.status == status)
            .order_by(Booking.date_from.asc())
            .all()
        )
        await send_admin_booking_cards(
            message,
            bookings,
            f"Заявок со статусом {BOOKING_STATUS_LABELS.get(status, status)} нет.",
        )
        return

    if text.startswith("💰 В ожидании оплаты"):
        bookings = (
            session.query(Booking)
            .filter(Booking.status == BookingStatus.AWAITING_PAYMENT.value)
            .order_by(Booking.date_from.asc())
            .all()
        )
        await send_admin_booking_cards(message, bookings, "Заявок в ожидании оплаты нет.")
        return

    label = re.sub(r"\s+\(\d+\)$", "", text)
    room_type = None
    for candidate, room_name in room_type_names.items():
        if label == room_name:
            room_type = candidate
            break
    if room_type:
        bookings = (
            session.query(Booking)
            .filter(
                Booking.room_type == room_type,
                Booking.status.in_(ACTIVE_BOOKING_STATUSES),
            )
            .order_by(Booking.date_from.asc())
            .all()
        )
        await send_admin_booking_cards(
            message,
            bookings,
            f"Активных заявок для {room_type_names.get(room_type, room_type)} нет.",
        )
        return

    await message.answer("Выберите пункт кнопкой снизу.")


async def send_admin_booking_cards(target_message, bookings, empty_text: str):
    if not bookings:
        await target_message.answer(
            empty_text,
            reply_markup=admin_menu_keyboard(),
        )
        return
    for booking in bookings[:20]:
        session.refresh(booking)
        children_beds = get_children_beds(booking)
        total_people = booking.adults + sum(children_beds)
        calculated_total = await calculate_revenue(booking.room_type, total_people, booking.date_from, booking.date_to)
        balance = calculate_booking_balance(booking, calculated_total)
        total = balance["total"]
        paid = balance["paid"]
        remaining = balance["remaining"]
        status_text = payment_status_label(balance) or BOOKING_STATUS_LABELS.get(booking.status, booking.status)
        text = (
            f"📌 Заявка #{booking.id}\n"
            f"👤 Клиент: {booking.full_name or 'не указан'} (@{booking.username or 'без ника'})\n"
            f"📞 Телефон: {booking.phone or 'не указан'}\n"
            f"🏠 Номер: {room_type_names.get(booking.room_type, booking.room_type)}\n"
            f"📅 Даты: {booking.date_from.strftime('%d.%m.%Y')} - {booking.date_to.strftime('%d.%m.%Y')}\n"
            f"👥 Гости: {total_people} (взрослые: {booking.adults}, дети: {booking.children})\n"
            f"💰 Сумма: {total}₽\n"
            f"✅ Внесено: {paid}₽\n"
            f"🔐 Требуемая предоплата: {balance['required_prepayment']}₽\n"
            f"↩️ Возвращено: {balance['refunds']}₽\n"
            f"🧾 Осталось: {remaining}₽\n"
            f"📍 Статус: {status_text}\n"
            f"💬 Комментарий: {booking.comment or 'нет'}\n"
            f"📝 Админ-комментарий: {booking.admin_comment or 'нет'}"
        )
        await target_message.answer(
            text,
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="Изменить статус", callback_data=f"admin_change_status_{booking.id}")],
                    [InlineKeyboardButton(text="Редактировать", callback_data=f"admin_edit_booking_{booking.id}")],
                    [InlineKeyboardButton(text="💳 Изменить предоплату", callback_data=f"admin_update_payment_{booking.id}")],
                ]
            ),
        )


@router.callback_query(F.data.startswith("admin_filter_status_"))
async def admin_filter_status(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав", show_alert=True)
        return
    status = callback.data.replace("admin_filter_status_", "", 1)
    bookings = session.query(Booking).filter(Booking.status == status).order_by(Booking.date_from.asc()).all()
    await callback.message.delete()
    await send_admin_booking_cards(callback.message, bookings, f"Заявок со статусом {BOOKING_STATUS_LABELS.get(status, status)} нет.")
    await callback.answer()


@router.callback_query(F.data == "admin_search_booking")
async def admin_search_booking(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав", show_alert=True)
        return
    await callback.message.edit_text(
        "Введите имя, телефон, username или ID заявки для поиска:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ К заявкам", callback_data="admin_view_bookings")]]
        ),
    )
    await state.set_state(BookingStates.waiting_for_admin_booking_search)
    await callback.answer()


@router.message(BookingStates.waiting_for_admin_booking_search, F.text)
async def process_admin_booking_search(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    from sqlalchemy import or_

    query = message.text.strip()
    filters = [
        Booking.full_name.ilike(f"%{query}%"),
        Booking.phone.ilike(f"%{query}%"),
        Booking.username.ilike(f"%{query}%"),
    ]
    if query.isdigit():
        filters.append(Booking.id == int(query))
    bookings = session.query(Booking).filter(or_(*filters)).order_by(Booking.id.desc()).all()
    await state.clear()
    await send_admin_booking_cards(message, bookings, "Заявки не найдены.")


@router.callback_query(F.data.startswith("admin_change_status_"))
async def admin_change_status(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав", show_alert=True)
        return
    booking_id = int(callback.data.split("_")[3])
    rows = []
    for status in [
        BookingStatus.NEW.value,
        BookingStatus.PENDING.value,
        BookingStatus.AWAITING_PAYMENT.value,
        BookingStatus.PAID.value,
        BookingStatus.AWAITING_CANCELLATION.value,
        BookingStatus.CANCELED.value,
        BookingStatus.REJECTED.value,
    ]:
        rows.append([InlineKeyboardButton(text=BOOKING_STATUS_LABELS.get(status, status), callback_data=f"admin_set_status_{booking_id}_{status}")])
    rows.append([InlineKeyboardButton(text="⬅️ К заявкам", callback_data="admin_view_bookings")])
    await callback.message.answer("Выберите новый статус:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await callback.answer()


@router.callback_query(F.data.startswith("admin_set_status_"))
async def admin_set_status(callback: CallbackQuery):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав", show_alert=True)
        return
    raw = callback.data.replace("admin_set_status_", "", 1)
    booking_id_text, status = raw.split("_", 1)
    booking = session.query(Booking).filter_by(id=int(booking_id_text)).first()
    if not booking:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    booking.status = status
    if status != BookingStatus.AWAITING_PAYMENT.value:
        booking.payment_deadline = None
    session.add(AdminLog(admin_id=callback.from_user.id, booking_id=booking.id, action=f"Изменил статус заявки #{booking.id} на {status}"))
    session.commit()
    clear_booked_dates_cache()
    await callback.message.edit_text(f"Статус заявки #{booking.id} изменен на {BOOKING_STATUS_LABELS.get(status, status)}")
    await callback.answer()


@router.callback_query(F.data == "admin_edit_booking")
async def admin_edit_booking_start(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав", show_alert=True)
        return
    await callback.message.edit_text(
        "Введите: ID; дата заезда ДД.ММ.ГГГГ; дата выезда ДД.ММ.ГГГГ; тип номера\n"
        "Пример: 12; 15.07.2026; 17.07.2026; 4",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="⬅️ К заявкам", callback_data="admin_view_bookings")]]
        ),
    )
    await state.set_state(BookingStates.waiting_for_admin_booking_edit)
    await callback.answer()


@router.callback_query(F.data.startswith("admin_edit_booking_"))
async def admin_edit_booking_prefill(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав", show_alert=True)
        return
    booking_id = callback.data.replace("admin_edit_booking_", "", 1)
    await callback.message.answer(
        f"Введите новые данные для заявки #{booking_id}:\n"
        f"{booking_id}; дата заезда ДД.ММ.ГГГГ; дата выезда ДД.ММ.ГГГГ; тип номера"
    )
    await state.set_state(BookingStates.waiting_for_admin_booking_edit)
    await callback.answer()


@router.message(BookingStates.waiting_for_admin_booking_edit, F.text)
async def process_admin_booking_edit(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    try:
        booking_id_text, start_text, end_text, room_type = [part.strip() for part in message.text.split(";", 3)]
        booking = session.query(Booking).filter_by(id=int(booking_id_text)).first()
        if not booking:
            await message.answer("Заявка не найдена.")
            return
        if room_type not in room_type_names:
            await message.answer("Неизвестный тип номера.")
            return
        start_date = datetime.strptime(start_text, "%d.%m.%Y").date()
        end_date = datetime.strptime(end_text, "%d.%m.%Y").date()
        if end_date <= start_date:
            await message.answer("Дата выезда должна быть позже даты заезда.")
            return
        booking.date_from = start_date
        booking.date_to = end_date
        booking.room_type = room_type
        session.add(AdminLog(admin_id=message.from_user.id, booking_id=booking.id, action=f"Изменил даты/номер заявки #{booking.id}"))
        session.commit()
        clear_booked_dates_cache()
        await state.clear()
        await message.answer(f"Заявка #{booking.id} обновлена.")
    except Exception as error:
        await message.answer(f"Не удалось обновить заявку: {error}")



@router.callback_query(F.data == "admin_view_awaiting_payment")
async def admin_view_awaiting_payment(callback: CallbackQuery, state: FSMContext):
    from config import room_type_names, ADMIN_CHAT_ID, PRICE_PER_ADULT, ALL_ROOMS_PRICE

    try:
        if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
            await callback.answer("У вас нет прав на это действие", show_alert=True)
            return

        await state.update_data(previous_menu="admin_view_bookings")

        try:
            await callback.message.delete()
        except TelegramBadRequest as e:
            logging.warning(f"Не удалось удалить сообщение: {str(e)}")

        bookings = (
            session.query(Booking)
            .filter(
                Booking.status.in_(
                    ["awaiting_payment", "awaiting_payment_confirmation"]
                )
            )
            .order_by(Booking.date_from.asc())
            .all()
        )

        if not bookings:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅ Назад", callback_data="admin_view_bookings"
                        )
                    ]
                ]
            )
            await callback.message.answer(
                f"📋 Нет заявок в ожидании оплаты или подтверждения.",
                reply_markup=keyboard,
            )
            session.add(
                AdminLog(
                    admin_id=callback.from_user.id,
                    action="Просмотрел заявки в ожидании оплаты",
                )
            )
            session.commit()
            logging.info(
                f"Админ {callback.from_user.id} просмотрел заявки в ожидании оплаты: count=0"
            )
            await callback.answer()
            return

        for idx, booking in enumerate(bookings):
            status = (
                "💰 Ожидает оплаты"
                if booking.status == "awaiting_payment"
                else "📸 Ожидает подтверждения оплаты"
            )
            nights = (booking.date_to - booking.date_from).days
            children_beds = get_children_beds(booking)
            children_needing_beds = sum(children_beds)
            total_people = booking.adults + children_needing_beds
            total = await calculate_revenue(
                booking.room_type, total_people, booking.date_from, booking.date_to
            )
            report_text = (
                f"📌 Заявка #{booking.id}\n"
                f"👤 {booking.full_name} (@{booking.username or 'без ника'})\n"
                f"📞 {booking.phone}\n"
                f"🏠 {room_type_names[booking.room_type]}\n"
                f"📅 Заезд: {booking.date_from.strftime('%d.%m.%Y')} после 14:00\n"
                f"📅 Выезд: {booking.date_to.strftime('%d.%m.%Y')} до 12:00\n"
                f"👨‍👩‍👧‍👦 Всего человек: {total_people} (взрослых: {booking.adults}, детей: {booking.children}, из них {children_needing_beds} с местами)\n"
                f"💰 Сумма: {total}₽\n"
                f"💬 Комментарий клиента: {booking.comment}\n"
                f"📝 Комментарий админа: {booking.admin_comment or 'Отсутствует'}\n"
                f"Статус: {status}\n"
                f"⏰ Срок оплаты: {booking.payment_deadline.strftime('%d.%m.%Y %H:%M') if booking.payment_deadline else 'Ожидает проверки'}\n"
            )

            buttons = [
                [
                    InlineKeyboardButton(
                        text="💸 Оплата получена",
                        callback_data=f"admin_paid_{booking.id}",
                    )
                ]
            ]
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="💬 Ответить", callback_data=f"admin_reply_{booking.id}"
                    ),
                    InlineKeyboardButton(
                        text="🗑️ Удалить заявку",
                        callback_data=f"admin_delete_{booking.id}",
                    ),
                ]
            )
            buttons.append(
                [
                    InlineKeyboardButton(
                        text="✍️ Добавить/изменить комментарий",
                        callback_data=f"admin_add_comment_{booking.id}",
                    )
                ]
            )

            if idx == len(bookings) - 1:
                buttons.append(
                    [
                        InlineKeyboardButton(
                            text="⬅ Назад", callback_data="admin_view_bookings"
                        )
                    ]
                )

            kb = InlineKeyboardMarkup(inline_keyboard=buttons)
            await callback.message.answer(
                report_text, parse_mode="HTML", reply_markup=kb
            )

        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action="Просмотрел заявки в ожидании оплаты",
            )
        )
        session.commit()
        logging.info(
            f"Админ {callback.from_user.id} просмотрел заявки в ожидании оплаты: count={len(bookings)}"
        )
        await callback.answer()
    except Exception as e:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⬅ Назад", callback_data="admin_view_bookings"
                    )
                ]
            ]
        )
        await callback.message.answer(f"❗ Произошла ошибка: {str(e)}", reply_markup=kb)
        logging.error(
            f"Ошибка в admin_view_awaiting_payment: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer()


@router.callback_query(F.data == "admin_view_calendar")
async def admin_view_calendar(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    await state.update_data(previous_menu="admin")

    try:
        await show_admin_calendar(
            callback.message,
            state,
            room_type="2",
            month=6,
        )
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action="Просмотрел календарь для 2",
            )
        )
        session.commit()
    except Exception as e:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
            ]
        )
        try:
            await callback.message.edit_text(
                f"⚠️ Ошибка при загрузке календаря: {str(e)}", reply_markup=kb
            )
        except TelegramBadRequest as e:
            if "there is no text in the message to edit" in str(e):
                await callback.message.delete()
                await callback.message.answer(
                    f"⚠️ Ошибка при загрузке календаря: {str(e)}", reply_markup=kb
                )
            else:
                raise
        logging.error(
            f"Ошибка в admin_view_calendar: {str(e)}\n{traceback.format_exc()}"
        )
    await callback.answer()


async def show_admin_calendar(
    target_message,
    state: FSMContext,
    room_type: str,
    year: int | None = None,
    month: int = 6,
    edit: bool = False,
):
    current_year = year or session.query(BookingYear).first().year
    duration = 1
    if room_type not in room_type_names:
        room_type = "2"

    await state.update_data(
        is_admin_mode=True,
        room_type=room_type,
        admin_calendar_year=current_year,
        admin_calendar_month=month,
    )
    await state.set_state(BookingStates.viewing_admin_calendar)

    occupied_dates, checkin_dates = await get_booked_dates(room_type)
    calendar = await generate_calendar(
        year=current_year,
        month=month,
        booked_dates=(occupied_dates, checkin_dates),
        room_type=room_type,
        duration=duration,
        is_admin_mode=True,
        state=state,
    )
    text = (
        f"📅 Свободные даты заезда для варианта «{room_type_names.get(room_type, room_type)}»:\n"
        f"🟢 — свободные даты для заезда\n"
        f"🔴 — занятые даты\n\n"
        "Вариант проживания выбирайте кнопками снизу."
    )

    if edit:
        try:
            sent_calendar = await target_message.edit_text(text, reply_markup=calendar)
        except TelegramBadRequest:
            sent_calendar = await target_message.answer(text, reply_markup=calendar)
    else:
        sent_calendar = await target_message.answer(text, reply_markup=calendar)

    data = await state.get_data()
    old_controls_id = data.get("admin_calendar_controls_message_id")
    if old_controls_id:
        try:
            await target_message.bot.delete_message(target_message.chat.id, old_controls_id)
        except TelegramBadRequest:
            pass
    controls = await target_message.answer(
        "Выберите вариант проживания кнопками снизу:",
        reply_markup=admin_calendar_room_keyboard(),
    )
    await state.update_data(admin_calendar_controls_message_id=controls.message_id)
    return sent_calendar


@router.callback_query(F.data.startswith("month_"))
async def paginate_calendar_admin(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    try:
        parts = callback.data.split("_")
        if len(parts) < 4:
            raise ValueError(f"Неверный формат callback: {callback.data}")
        year, month, room_type = int(parts[1]), int(parts[2]), parts[3]
        duration = 1

        # Получаем текущий год из базы
        current_booking_year = session.query(BookingYear).first().year

        # Ограничиваем навигацию текущим годом
        if year != current_booking_year:
            await callback.answer(
                f"Календарь доступен только для {current_booking_year}.",
                show_alert=True,
            )
            return
        if month not in [5, 6, 7, 8, 9]:
            await callback.answer(
                "Календарь доступен только для мая–сентября.", show_alert=True
            )
            return
        if room_type not in room_type_names:
            room_type = "2"
            logging.warning(f"Недопустимый room_type: {room_type}, установлен '2'")

        await state.update_data(is_admin_mode=True, room_type=room_type)

        occupied_dates, checkin_dates = await get_booked_dates(room_type)
        booked_dates = (occupied_dates, checkin_dates)

        await callback.bot.send_chat_action(
            chat_id=callback.message.chat.id, action="typing"
        )
        calendar = await generate_calendar(
            year=year,
            month=month,
            booked_dates=booked_dates,
            room_type=room_type,
            duration=duration,
            is_admin_mode=True,
            state=state,
        )
        await callback.message.edit_text(
            f"📅 Свободные даты заезда для варианта «{room_type_names.get(room_type, room_type)}»:\n"
            f"🟢 — свободные даты для заезда\n"
            f"🔴 — занятые даты\n\n"
            "Вариант проживания выбирайте кнопками снизу.",
            reply_markup=calendar,
        )
        await state.update_data(
            admin_calendar_year=year,
            admin_calendar_month=month,
        )
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Переключил календарь на {room_type}, {year}-{month}",
            )
        )
        session.commit()
    except Exception as e:
        logging.error(
            f"Ошибка в paginate_calendar_admin для year {year}, month {month}: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.message.answer(
            "⚠️ Ошибка при загрузке календаря. Попробуйте снова или свяжитесь с поддержкой."
        )
        await state.clear()
    await callback.answer()


@router.callback_query(F.data.startswith("room_"))
async def switch_room_admin(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    try:
        parts = callback.data.split("_")
        if len(parts) != 4:
            raise ValueError(f"Неверный формат callback: {callback.data}")
        room_type, year, month = parts[1], int(parts[2]), int(parts[3])
        duration = 1

        # Получаем текущий год из базы
        current_booking_year = session.query(BookingYear).first().year

        if year != current_booking_year:
            await callback.answer(
                f"Календарь доступен только для {current_booking_year}.",
                show_alert=True,
            )
            return
        if month not in [5, 6, 7, 8, 9]:
            await callback.answer(
                "Календарь доступен только для мая–сентября.", show_alert=True
            )
            return
        if room_type not in room_type_names:
            room_type = "2"
            logging.warning(f"Недопустимый room_type: {room_type}, установлен '2'")

        await state.update_data(is_admin_mode=True, room_type=room_type)

        occupied_dates, checkin_dates = await get_booked_dates(room_type)
        booked_dates = (occupied_dates, checkin_dates)

        await callback.bot.send_chat_action(
            chat_id=callback.message.chat.id, action="typing"
        )
        calendar = await generate_calendar(
            year=year,
            month=month,
            booked_dates=booked_dates,
            room_type=room_type,
            duration=duration,
            is_admin_mode=True,
        )
        try:
            await callback.message.edit_text(
                f"📅 Свободные даты заезда для варианта «{room_type_names.get(room_type, room_type)}»:\n"
                f"🟢 — свободные даты для заезда\n"
                f"🔴 — занятые даты",
                reply_markup=calendar,
            )
        except TelegramBadRequest as e:
            if "query is too old" in str(e):
                await callback.message.answer(
                    f"📅 Свободные даты заезда для варианта «{room_type_names.get(room_type, room_type)}»:\n"
                    f"🟢 — свободные даты для заезда\n"
                    f"🔴 — занятые даты",
                    reply_markup=calendar,
                )
                logging.info(
                    f"Повторная отправка сообщения из-за устаревшего query для room_type {room_type}"
                )
            else:
                raise
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Переключил номер на {room_type}, {year}-{month}",
            )
        )
        session.commit()
    except Exception as e:
        logging.error(
            f"Ошибка в switch_room_admin для room_type {room_type}, year {year}, month {month}: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.message.answer(
            "⚠️ Ошибка при переключении номера. Попробуйте снова."
        )
        await state.clear()
    await callback.answer()


@router.message(BookingStates.viewing_admin_calendar, F.text)
async def process_admin_calendar_room_reply(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        return
    text = message.text.strip()
    if text in {BTN_MAIN_MENU, BTN_BACK}:
        await show_main_menu_message(message, state)
        return
    if text == BTN_ADMIN:
        await admin_panel(message, state)
        return

    room_type = next(
        (
            candidate
            for candidate, label in room_type_names.items()
            if text == label
        ),
        None,
    )
    if not room_type:
        await message.answer(
            "Выберите вариант проживания кнопкой снизу.",
            reply_markup=admin_calendar_room_keyboard(),
        )
        return

    data = await state.get_data()
    year = int(data.get("admin_calendar_year") or session.query(BookingYear).first().year)
    month = int(data.get("admin_calendar_month") or 6)
    await show_admin_calendar(
        message,
        state,
        room_type=room_type,
        year=year,
        month=month,
    )
    session.add(
        AdminLog(
            admin_id=message.from_user.id,
            action=f"Переключил календарь через нижнюю клавиатуру на {room_type}, {year}-{month}",
        )
    )
    session.commit()


@router.callback_query(F.data.startswith("view_stats_year_"))
async def admin_stats(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    await state.update_data(previous_menu="admin")
    selected_year = int(callback.data.split("_")[3])
    season_start = date(selected_year, 6, 1)
    season_end = date(selected_year, 8, 31)
    month_names = {6: "Июнь", 7: "Июль", 8: "Август"}

    bookings = (
        session.query(Booking)
        .filter(
            Booking.status == BookingStatus.PAID.value,
            Booking.date_from >= season_start,
            Booking.date_from <= season_end,
        )
        .order_by(Booking.date_from.asc())
        .all()
    )
    logging.info(
        "Stats for %s: paid bookings=%s ids=%s",
        selected_year,
        len(bookings),
        [booking.id for booking in bookings],
    )

    monthly = {
        month: {"revenue": 0, "bookings": 0, "people": 0, "nights": 0, "occupied": 0, "details": []}
        for month in [6, 7, 8]
    }
    total_revenue = 0
    total_people = 0
    total_nights = 0
    occupied_places = 0
    total_paid = 0
    total_refunds = 0
    total_remaining = 0
    total_discounts = 0
    total_extra_services = 0

    for booking in bookings:
        nights = max((booking.date_to - booking.date_from).days, 0)
        children_beds = get_children_beds(booking)
        children_needing_beds = sum(children_beds)
        total_people_booking = booking.adults + children_needing_beds
        calculated_revenue = await calculate_revenue(
            booking.room_type,
            total_people_booking,
            booking.date_from,
            booking.date_to,
        )
        balance = calculate_booking_balance(booking, calculated_revenue)
        revenue = balance["total"]
        month = booking.date_from.month
        if month not in monthly:
            continue

        monthly[month]["revenue"] += revenue
        monthly[month]["bookings"] += 1
        monthly[month]["people"] += total_people_booking
        monthly[month]["nights"] += nights
        monthly[month]["occupied"] += total_people_booking * nights
        monthly[month]["details"].append(
            f"#{booking.id}: {room_type_names.get(booking.room_type, booking.room_type)}, "
            f"{booking.date_from.strftime('%d.%m')}-{booking.date_to.strftime('%d.%m')}, "
            f"{total_people_booking} чел., {revenue} ₽"
        )
        total_revenue += revenue
        total_paid += balance["paid"]
        total_refunds += balance["refunds"]
        total_remaining += balance["remaining"]
        total_discounts += balance["discount"]
        total_extra_services += balance["extra_services"]
        total_people += total_people_booking
        total_nights += nights
        occupied_places += total_people_booking * nights

    total_bookings = len(bookings)
    total_capacity = 14 * ((season_end - season_start).days + 1)
    occupancy_rate = (occupied_places / total_capacity * 100) if total_capacity else 0
    average_check = total_revenue / total_bookings if total_bookings else 0
    average_per_guest = total_revenue / total_people if total_people else 0

    max_revenue = max([monthly[m]["revenue"] for m in monthly] + [1])
    report_lines = [
        f"📊 Статистика сезона {selected_year}",
        "",
        f"✅ Оплаченных заявок: {total_bookings}",
        f"💰 Выручка: {total_revenue} ₽",
        f"✅ Внесено: {total_paid} ₽",
        f"↩️ Возвращено: {total_refunds} ₽",
        f"🧾 Остаток к оплате: {total_remaining} ₽",
        f"🏷 Скидки: {total_discounts} ₽",
        f"➕ Дополнительные услуги: {total_extra_services} ₽",
        f"👥 Гостей: {total_people}",
        f"🌙 Ночей: {total_nights}",
        f"📈 Загрузка: {occupancy_rate:.1f}%",
        f"🧾 Средний чек: {average_check:.0f} ₽",
        f"👤 Средний доход на гостя: {average_per_guest:.0f} ₽",
        "",
        "📅 По месяцам:",
    ]

    for month in [6, 7, 8]:
        revenue = monthly[month]["revenue"]
        bar_len = round((revenue / max_revenue) * 12) if revenue else 0
        bar = "█" * bar_len + "░" * (12 - bar_len)
        report_lines.append(
            f"{month_names[month]}: {bar} {revenue} ₽, "
            f"броней {monthly[month]['bookings']}, гостей {monthly[month]['people']}"
        )

    if total_bookings:
        report_lines.append("")
        report_lines.append("📌 Заявки:")
        for month in [6, 7, 8]:
            report_lines.append(f"\n{month_names[month]}:")
            if monthly[month]["details"]:
                report_lines.extend(monthly[month]["details"])
            else:
                report_lines.append("нет оплаченных заявок")
    else:
        report_lines.extend(["", "Пока нет оплаченных заявок для статистики."])

    kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]]
    )
    await callback.message.edit_text("\n".join(report_lines), reply_markup=kb)

    if total_bookings:
        try:
            import tempfile
            import matplotlib

            matplotlib.use("Agg")
            import matplotlib.pyplot as plt

            months = [month_names[m] for m in [6, 7, 8]]
            revenues = [monthly[m]["revenue"] for m in [6, 7, 8]]
            booking_counts = [monthly[m]["bookings"] for m in [6, 7, 8]]
            month_occupancy = []
            for m in [6, 7, 8]:
                days_in_month = 30 if m == 6 else 31
                capacity = 14 * days_in_month
                month_occupancy.append((monthly[m]["occupied"] / capacity * 100) if capacity else 0)

            fig, axes = plt.subplots(3, 1, figsize=(8, 9))
            fig.suptitle(f"Статистика сезона {selected_year}", fontsize=16, fontweight="bold")
            axes[0].bar(months, revenues, color="#2f80ed")
            axes[0].set_title("Выручка, ₽")
            axes[1].bar(months, booking_counts, color="#27ae60")
            axes[1].set_title("Количество броней")
            axes[2].bar(months, month_occupancy, color="#f2994a")
            axes[2].set_title("Загрузка, %")
            axes[2].set_ylim(0, max(100, max(month_occupancy) + 10))
            fig.tight_layout(rect=[0, 0.03, 1, 0.95])

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                chart_path = tmp.name
            fig.savefig(chart_path, dpi=160)
            plt.close(fig)
            await callback.message.answer_photo(
                FSInputFile(chart_path),
                caption=f"📊 График статистики за {selected_year}",
            )
            try:
                os.remove(chart_path)
            except OSError:
                pass
        except Exception as e:
            logging.warning("Failed to render stats PNG chart: %s", e)
            await callback.message.answer(
                "PNG-график не удалось построить, текстовый отчет уже отправлен."
            )

    session.add(
        AdminLog(
            admin_id=callback.from_user.id,
            action=f"Просмотр статистики за {selected_year}",
        )
    )
    session.commit()
    await callback.answer()


@router.callback_query(F.data == "admin_view_users")
async def admin_view_users(callback: CallbackQuery, state: FSMContext):
    from config import ADMIN_CHAT_ID

    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    await state.update_data(
        previous_menu="admin", page=1
    )  # Сбрасываем на первую страницу

    try:
        # Запрашиваем пользователей, принявших соглашение
        users = session.query(User).filter_by(rules_accepted=True).all()

        if not users:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                ]
            )
            await callback.message.edit_text(
                "👥 Нет пользователей, принявших соглашение.", reply_markup=kb
            )
            session.add(
                AdminLog(
                    admin_id=callback.from_user.id,
                    action="Просмотрел список пользователей с согласием",
                )
            )
            session.commit()
            logging.info(
                f"Админ {callback.from_user.id} просмотрел список пользователей с согласием: count=0"
            )
            await callback.answer()
            return

        # Формируем список пользователей
        def escape_markdown(text: str) -> str:
            """Экранирование специальных символов для MarkdownV2."""
            if not text:
                return "Аноним"
            chars = [
                "_",
                "*",
                "[",
                "]",
                "(",
                ")",
                "~",
                "`",
                ">",
                "#",
                "+",
                "-",
                "=",
                "|",
                "{",
                "}",
                ".",
                "!",
            ]
            for char in chars:
                text = text.replace(char, f"\\{char}")
            return text

        user_list = []
        for user in users:
            try:
                chat = await callback.message.bot.get_chat(user.user_id)
                username = escape_markdown(chat.username) if chat.username else "Аноним"
            except Exception as e:
                logging.warning(
                    f"Не удалось получить username для user_id {user.user_id}: {str(e)}"
                )
                username = "Аноним"
            user_list.append(
                f"👤 @{username} \\(ID: `{user.user_id}`\\) [профиль](tg://user?id={user.user_id})"
            )

        # Пагинация
        page_size = 10
        data = await state.get_data()
        page = data.get("page", 1)
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_users = user_list[start_idx:end_idx]

        total_pages = max(1, (len(user_list) + page_size - 1) // page_size)
        text = (
            f"👥 *Пользователи, принявшие соглашение* \\({len(users)}\\) \\(стр\\. {page}/{total_pages}\\):\n\n"
            + "\n".join(paginated_users)
        )

        # Формируем кнопки пагинации
        buttons = []
        if start_idx > 0:
            buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Пред.", callback_data=f"admin_view_users_page_{page-1}"
                )
            )
        if end_idx < len(user_list):
            buttons.append(
                InlineKeyboardButton(
                    text="След. ➡️", callback_data=f"admin_view_users_page_{page+1}"
                )
            )
        buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin"))
        kb = InlineKeyboardMarkup(
            inline_keyboard=(
                [buttons]
                if buttons
                else [[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]]
            )
        )

        await callback.message.edit_text(text, parse_mode="MarkdownV2", reply_markup=kb)
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Просмотрел список пользователей с согласием (страница {page}/{total_pages})",
            )
        )
        session.commit()
        logging.info(
            f"Админ {callback.from_user.id} просмотрел список пользователей с согласием: count={len(users)}, page={page}/{total_pages}"
        )
    except Exception as e:
        logging.error(f"Ошибка в admin_view_users: {str(e)}\n{traceback.format_exc()}")
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
            ]
        )
        await callback.message.edit_text(f"❗ Ошибка: {str(e)}", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.regexp(r"admin_view_users_page_(\d+)"))
async def admin_view_users_page(callback: CallbackQuery, state: FSMContext):
    from config import ADMIN_CHAT_ID

    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    try:
        page = int(callback.data.split("_")[4])
        if page < 1:
            await callback.answer("Недопустимый номер страницы.", show_alert=True)
            return

        await state.update_data(page=page)

        # Запрашиваем пользователей, принявших соглашение
        users = session.query(User).filter_by(rules_accepted=True).all()

        if not users:
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                ]
            )
            await callback.message.edit_text(
                "👥 Нет пользователей, принявших соглашение.", reply_markup=kb
            )
            session.add(
                AdminLog(
                    admin_id=callback.from_user.id,
                    action="Просмотрел список пользователей с согласием",
                )
            )
            session.commit()
            logging.info(
                f"Админ {callback.from_user.id} просмотрел список пользователей с согласием: count=0"
            )
            await callback.answer()
            return

        # Формируем список пользователей
        def escape_markdown(text: str) -> str:
            """Экранирование специальных символов для MarkdownV2."""
            if not text:
                return "Аноним"
            chars = [
                "_",
                "*",
                "[",
                "]",
                "(",
                ")",
                "~",
                "`",
                ">",
                "#",
                "+",
                "-",
                "=",
                "|",
                "{",
                "}",
                ".",
                "!",
            ]
            for char in chars:
                text = text.replace(char, f"\\{char}")
            return text

        user_list = []
        for user in users:
            try:
                chat = await callback.message.bot.get_chat(user.user_id)
                username = escape_markdown(chat.username) if chat.username else "Аноним"
            except Exception as e:
                logging.warning(
                    f"Не удалось получить username для user_id {user.user_id}: {str(e)}"
                )
                username = "Аноним"
            user_list.append(
                f"👤 @{username} \\(ID: `{user.user_id}`\\) [профиль](tg://user?id={user.user_id})"
            )

        # Пагинация
        page_size = 10
        start_idx = (page - 1) * page_size
        end_idx = start_idx + page_size
        paginated_users = user_list[start_idx:end_idx]

        total_pages = max(1, (len(user_list) + page_size - 1) // page_size)
        if page > total_pages:
            await callback.answer("Эта страница недоступна.", show_alert=True)
            return

        text = (
            f"👥 *Пользователи, принявшие соглашение* \\({len(users)}\\) \\(стр\\. {page}/{total_pages}\\):\n\n"
            + "\n".join(paginated_users)
        )

        # Формируем кнопки пагинации
        buttons = []
        if start_idx > 0:
            buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Пред.", callback_data=f"admin_view_users_page_{page-1}"
                )
            )
        if end_idx < len(user_list):
            buttons.append(
                InlineKeyboardButton(
                    text="След. ➡️", callback_data=f"admin_view_users_page_{page+1}"
                )
            )
        buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data="admin"))
        kb = InlineKeyboardMarkup(
            inline_keyboard=(
                [buttons]
                if buttons
                else [[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]]
            )
        )

        await callback.message.edit_text(text, parse_mode="MarkdownV2", reply_markup=kb)
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Просмотрел список пользователей с согласием (страница {page}/{total_pages})",
            )
        )
        session.commit()
        logging.info(
            f"Админ {callback.from_user.id} просмотрел список пользователей с согласием: count={len(users)}, page={page}/{total_pages}"
        )
    except Exception as e:
        logging.error(
            f"Ошибка в admin_view_users_page: {str(e)}\n{traceback.format_exc()}"
        )
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
            ]
        )
        await callback.message.edit_text(f"❗ Ошибка: {str(e)}", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "admin_download_db")
async def download_database(callback: CallbackQuery, state: FSMContext):
    from config import ADMIN_CHAT_ID

    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return

    await state.update_data(previous_menu="admin")

    # Получаем текущий год и статус сезона
    booking_year = session.query(BookingYear).first()
    current_year = booking_year.year if booking_year else 2025
    season = session.query(BookingSeason).first()
    season_status = "Активен" if season and season.is_active else "Завершён"

    try:
        db_file = str(DATABASE_PATH)
        if not os.path.exists(db_file):
            kb = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
                ]
            )
            await callback.message.edit_text(
                "❗ Файл базы данных не найден.", reply_markup=kb
            )
            logging.error(f"Файл базы данных {db_file} не найден")
            await callback.answer()
            return

        file = FSInputFile(
            db_file,
            filename=f"booking_db_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db",
        )
        await callback.message.answer_document(
            document=file, caption="📦 Файл базы данных booking.db"
        )

        # Формируем админ-панель
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📋 Активные заявки", callback_data="admin_view_bookings"
                    )
                ],
            [
                InlineKeyboardButton(
                    text="\U0001f4ac \u041e\u0431\u0440\u0430\u0449\u0435\u043d\u0438\u044f \u043f\u043e\u0434\u0434\u0435\u0440\u0436\u043a\u0438", callback_data="admin_support_tickets"
                )
            ],
                [
                    InlineKeyboardButton(
                        text="📅 Календарь бронирований",
                        callback_data="admin_view_calendar",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📊 Статистика за год",
                        callback_data="admin_select_stats_year",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📈 Изменить цены", callback_data="admin_change_prices"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="⭐ Управление отзывами",
                        callback_data="admin_manage_reviews",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📰 Создать новость", callback_data="admin_create_news"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💾 Скачать базу данных", callback_data="admin_download_db"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📄 Скачать лог", callback_data="admin_download_log"
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"📅 Изменить год бронирования ({current_year})",
                        callback_data="admin_change_booking_year",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=f"🌞 Управление сезоном ({season_status})",
                        callback_data="admin_manage_season",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="👥 Пользователи с согласием",
                        callback_data="admin_view_users",
                    )
                ],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_main")],
            ]
        )
        new_text = "👨‍💼 Панель администратора:"

        # Проверяем, нужно ли обновлять сообщение
        current_text = callback.message.text or ""
        current_reply_markup = callback.message.reply_markup
        if current_text != new_text or current_reply_markup != kb:
            try:
                await callback.message.edit_text(new_text, reply_markup=kb)
            except TelegramBadRequest as e:
                if "message is not modified" in str(e):
                    logging.debug(
                        f"Сообщение админ-панели не обновлено: текст и клавиатура не изменились"
                    )
                else:
                    raise
        else:
            logging.debug(
                f"Сообщение админ-панели не обновлено: текст и клавиатура идентичны"
            )

        session.add(
            AdminLog(admin_id=callback.from_user.id, action="Скачал базу данных")
        )
        session.commit()
        logging.info(f"Админ {callback.from_user.id} скачал базу данных")
    except Exception as e:
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin")]
            ]
        )
        await callback.message.edit_text(
            f"❗ Ошибка при скачивании базы данных: {str(e)}", reply_markup=kb
        )
        logging.error(f"Ошибка в download_database: {str(e)}\n{traceback.format_exc()}")
    await callback.answer()


async def check_payment_deadlines(bot: Bot):
    from utils import clear_booked_dates_cache

    logging.info("Задача check_payment_deadlines запущена")
    while True:
        try:
            now = datetime.now()
            logging.info(f"Проверка сроков оплаты: {now}")
            with session as db_session:
                bookings = (
                    db_session.query(Booking)
                    .filter(
                        Booking.status
                        == "awaiting_payment",  # Проверяем только awaiting_payment
                        Booking.payment_deadline.isnot(None),
                    )
                    .all()
                )
                expired_bookings = []
                reminded_bookings = set()
                for booking in bookings:
                    reminder_time = booking.payment_deadline - timedelta(minutes=15)
                    if (
                        reminder_time <= now < booking.payment_deadline
                        and booking.id not in reminded_bookings
                    ):
                        try:
                            await bot.send_message(
                                booking.user_id,
                                f"⏰ Напоминание: у вас осталось 15 минут, чтобы оплатить заявку #{booking.id}!",
                            )
                            reminded_bookings.add(booking.id)
                            logging.info(
                                f"Отправлено напоминание пользователю {booking.user_id} о заявке #{booking.id}"
                            )
                        except Exception as e:
                            logging.error(
                                f"Ошибка при отправке напоминания для заявки #{booking.id}: {str(e)}"
                            )
                    if now > booking.payment_deadline:
                        expired_bookings.append(booking)
                        booking.status = BookingStatus.CANCELLED.value
                        db_session.commit()
                        clear_booked_dates_cache()
                        await bot.send_message(
                            booking.user_id,
                            f"⏰ Время на оплату заявки #{booking.id} истекло. Заявка отменена.\n"
                            f"Пожалуйста, создайте новую заявку, если вы всё ещё хотите забронировать.",
                        )
                        await bot.send_message(
                            ADMIN_CHAT_ID,
                            f"📌 Заявка #{booking.id} автоматически отменена из-за истечения времени оплаты.",
                        )
                        logging.info(
                            f"Заявка #{booking.id} отменена: время на оплату истекло"
                        )
        except Exception as e:
            logging.error(
                f"Ошибка в check_payment_deadlines: {str(e)}\n{traceback.format_exc()}"
            )
        await asyncio.sleep(60)


def register_handlers(dp: Dispatcher, bot: Bot):
    logging.info("Регистрация обработчиков начата")
    dp.include_router(admin_router)
    dp.include_router(admin_prices_router)
    dp.include_router(admin_news_router)
    dp.include_router(my_bookings_router)
    dp.include_router(router)
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(check_payment_deadlines(bot))
        logging.info("Задача check_payment_deadlines успешно зарегистрирована")
    except Exception as e:
        logging.error(f"Ошибка при регистрации задач: {str(e)}")
        raise
    logging.info("Регистрация обработчиков завершена")


@router.callback_query(F.data.regexp(r"admin_confirm_(\d+)"))
async def confirm_booking_callback(callback: types.CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет прав на это действие", show_alert=True)
        return
    try:
        from utils import calculate_revenue, get_children_beds

        booking_id = int(callback.data.split("_")[2])
        booking = session.query(Booking).filter_by(id=booking_id).first()
        if not booking:
            await callback.answer(f"Заявка #{booking_id} не найдена.", show_alert=True)
            return
        booking.status = BookingStatus.AWAITING_PAYMENT.value
        now = datetime.now()
        booking.payment_deadline = now + timedelta(hours=PAYMENT_DEADLINE_HOURS)
        session.commit()
        clear_booked_dates_cache()
        logging.debug(
            f"Заявка #{booking_id} переведена в статус awaiting_payment, срок оплаты до {booking.payment_deadline}"
        )

        # Сохраняем booking_id в состоянии заранее
        await state.update_data(booking_id=booking_id)

        try:
            if booking.user_id:
                await callback.message.bot.get_chat(
                    booking.user_id
                )  # Проверка доступности чата
                children_beds = get_children_beds(booking)
                children_needing_beds = sum(children_beds)
                total_people = booking.adults + children_needing_beds
                total_price = await calculate_revenue(
                    booking.room_type, total_people, booking.date_from, booking.date_to
                )
                balance = calculate_booking_balance(booking, total_price)
                await callback.message.bot.send_message(
                    chat_id=booking.user_id,
                    text=(
                        f"✅ Ваша заявка #{booking.id} подтверждена!\n"
                        f"💰 Итоговая сумма: {balance['total']}₽\n"
                        f"🔐 Минимальная предоплата 50%: {balance['required_prepayment']}₽\n"
                        f"✅ Можно оплатить всю сумму сразу: {balance['total']}₽\n"
                        f"🧾 При частичной оплате остаток оплачивается по приезду.\n\n"
                        f"Реквизиты для оплаты: Сбербанк 2202206350763830\n"
                        f"⏰ На оплату у вас есть {PAYMENT_DEADLINE_HOURS} часа — до {booking.payment_deadline.strftime('%d.%m.%Y %H:%M')}.\n"
                        f"После оплаты отправьте скриншот чека."
                    ),
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[
                            [
                                InlineKeyboardButton(
                                    text="📸 Отправить скриншот",
                                    callback_data=f"send_screenshot_{booking.id}",
                                )
                            ]
                        ]
                    ),
                    parse_mode="HTML",
                )
                await state.set_state(BookingStates.waiting_for_payment_screenshot)
                logging.info(
                    f"Сообщение о подтверждении отправлено пользователю {booking.user_id} для заявки #{booking_id}"
                )
        except TelegramBadRequest as e:
            logging.error(
                f"TelegramBadRequest при отправке сообщения пользователю {booking.user_id} для заявки #{booking_id}: {str(e)}\n{traceback.format_exc()}"
            )
            await callback.message.answer(
                f"❗ Ошибка при отправке сообщения пользователю: {str(e)} (возможно, пользователь заблокировал бота)",
                parse_mode="HTML",
            )
        except Exception as e:
            logging.error(
                f"Неизвестная ошибка при отправке сообщения пользователю {booking.user_id} для заявки #{booking_id}: {str(e)}\n{traceback.format_exc()}"
            )
            await callback.message.answer(
                f"❗ Ошибка при отправке сообщения пользователю: {str(e)}",
                parse_mode="HTML",
            )
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer(
            f"Заявка #{booking_id} подтверждена ✅", parse_mode="HTML"
        )
        session.add(
            AdminLog(
                admin_id=callback.from_user.id,
                action=f"Подтвердил заявку #{booking_id}",
                booking_id=booking_id,
            )
        )
        session.commit()
        logging.info(f"Админ {callback.from_user.id} подтвердил заявку #{booking_id}")
    except ValueError as ve:
        logging.error(
            f"Неверный формат booking_id в callback.data={callback.data}: {str(ve)}"
        )
        await callback.answer("Неверный формат данных заявки.", show_alert=True)
    except Exception as e:
        logging.error(
            f"Ошибка в confirm_booking_callback для booking_id {booking_id}: {str(e)}\n{traceback.format_exc()}"
        )
        await callback.answer(f"Произошла ошибка: {str(e)}", show_alert=True)


@router.callback_query(F.data == "occupied")
async def handle_occupied_date(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state()
    data = await state.get_data()
    duration = data.get("duration", 1)
    start_date = (
        datetime.strptime(data["start_date"], "%Y-%m-%d").date()
        if "start_date" in data
        else None
    )
    expected_end_date = start_date + timedelta(days=duration) if start_date else None

    if current_state == BookingStates.choosing_month_for_end_date and expected_end_date:
        await callback.answer(
            f"У вас выбрана уже дата выезда {expected_end_date.strftime('%d.%m.%Y')} на основе выбранных {get_duration_text(duration)}.",
            show_alert=True,
        )
    else:
        await callback.answer("Дата недоступна, выберите другую.", show_alert=True)
    logging.info(
        f"Пользователь {callback.from_user.id} попытался выбрать занятую дату в состоянии {current_state}"
    )


@router.callback_query()
async def catch_all_callbacks(callback: CallbackQuery, state: FSMContext):
    current_state = await state.get_state() if state else "Unknown state"
    logging.warning(
        f"Необработанный callback: data={callback.data}, from_user={callback.from_user.id}, "
        f"message_id={callback.message.message_id}, state={current_state}, "
        f"message_text={callback.message.text or 'No text'}, "
        f"message_type={callback.message.content_type}"
    )
    await callback.answer(
        "⚠️ Действие не распознано. Пожалуйста, используйте меню или свяжитесь с поддержкой.",
        show_alert=True,
    )
