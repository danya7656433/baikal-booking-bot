import logging
from calendar import monthcalendar
from datetime import date, datetime, timedelta

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.states import BookingStates
from config import ADMIN_CHAT_ID
from database import AdminLog, BookingYear, RoomPriceOverride, session

router = Router()

RUSSIAN_MONTHS = {
    6: "Июнь",
    7: "Июль",
    8: "Август",
}
ALLOWED_MONTHS = [6, 7, 8]


def get_booking_year() -> int:
    booking_year = session.query(BookingYear).first()
    return booking_year.year if booking_year else date.today().year


def build_price_calendar(
    year: int,
    month: int,
    mode: str,
    selected_start: date | None = None,
) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=f"{RUSSIAN_MONTHS[month]} {year}", callback_data="ignore")],
        [
            InlineKeyboardButton(text="Пн", callback_data="ignore"),
            InlineKeyboardButton(text="Вт", callback_data="ignore"),
            InlineKeyboardButton(text="Ср", callback_data="ignore"),
            InlineKeyboardButton(text="Чт", callback_data="ignore"),
            InlineKeyboardButton(text="Пт", callback_data="ignore"),
            InlineKeyboardButton(text="Сб", callback_data="ignore"),
            InlineKeyboardButton(text="Вс", callback_data="ignore"),
        ],
    ]

    today = date.today()
    for week in monthcalendar(year, month):
        row = []
        for day in week:
            if day == 0:
                row.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
                continue

            current_date = date(year, month, day)
            is_locked = current_date < today
            if mode == "end" and selected_start and current_date < selected_start:
                is_locked = True

            if selected_start and current_date == selected_start:
                text = f"{day}✅"
            elif is_locked:
                text = f"{day}🔒"
            else:
                text = str(day)

            callback_data = (
                "locked"
                if is_locked
                else f"price_{mode}_{year}_{month}_{day}"
            )
            row.append(InlineKeyboardButton(text=text, callback_data=callback_data))
        rows.append(row)

    prev_month = month - 1
    next_month = month + 1
    nav = []
    if prev_month in ALLOWED_MONTHS:
        nav.append(
            InlineKeyboardButton(
                text=RUSSIAN_MONTHS[prev_month],
                callback_data=f"price_month_{mode}_{year}_{prev_month}",
            )
        )
    else:
        nav.append(InlineKeyboardButton(text=" ", callback_data="ignore"))

    nav.append(InlineKeyboardButton(text=" ", callback_data="ignore"))

    if next_month in ALLOWED_MONTHS:
        nav.append(
            InlineKeyboardButton(
                text=RUSSIAN_MONTHS[next_month],
                callback_data=f"price_month_{mode}_{year}_{next_month}",
            )
        )
    else:
        nav.append(InlineKeyboardButton(text=" ", callback_data="ignore"))
    rows.append(nav)

    rows.append([InlineKeyboardButton(text="⬅️ Админка", callback_data="admin")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_price_calendar(
    target,
    state: FSMContext,
    mode: str,
    year: int | None = None,
    month: int | None = None,
):
    data = await state.get_data()
    current_year = get_booking_year()
    year = year or current_year
    month = month or ALLOWED_MONTHS[0]
    selected_start = data.get("price_change_start_date")

    text = (
        f"📅 Выберите дату начала для цены {data['price_change_value']}₽"
        if mode == "start"
        else (
            f"📅 Выберите дату окончания.\n"
            f"Начало: {selected_start.strftime('%d.%m.%Y')}\n"
            f"Цена: {data['price_change_value']}₽"
        )
    )
    markup = build_price_calendar(year, month, mode, selected_start)

    if isinstance(target, CallbackQuery):
        await target.message.edit_text(text, reply_markup=markup)
    else:
        await target.answer(text, reply_markup=markup)


def apply_price_override(start_date: date, end_date: date, price: int):
    current_date = start_date
    while current_date <= end_date:
        existing_override = (
            session.query(RoomPriceOverride).filter_by(date=current_date).first()
        )
        if existing_override:
            existing_override.price = price
            existing_override.created_at = datetime.now()
        else:
            session.add(RoomPriceOverride(date=current_date, price=price))
        current_date += timedelta(days=1)
    session.commit()


@router.callback_query(F.data == "admin_change_prices")
async def admin_change_prices(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет доступа к этой команде.", show_alert=True)
        return

    await state.clear()
    await state.update_data(previous_menu="admin", is_admin_mode=False)
    await callback.message.edit_text(
        "💰 Введите новую цену за сутки за одно платное место.\n\n"
        "Например: 2000\n\n"
        "Эта цена будет применяться и к взрослому, и к ребёнку с отдельным местом.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Админка", callback_data="admin")]
            ]
        ),
    )
    await state.set_state(BookingStates.waiting_for_price_change_value)
    session.add(AdminLog(admin_id=callback.from_user.id, action="Начал изменение цен"))
    session.commit()
    await callback.answer()


@router.message(BookingStates.waiting_for_price_change_value)
async def process_price_change_value(message: Message, state: FSMContext):
    if str(message.from_user.id) != str(ADMIN_CHAT_ID):
        await message.answer("У вас нет доступа к этой команде.")
        return

    try:
        new_price = int(message.text.strip())
    except (TypeError, ValueError):
        await message.answer("Введите целое число, например 2000.")
        return

    if new_price <= 0:
        await message.answer("Цена должна быть положительным числом.")
        return

    await state.update_data(price_change_value=new_price)
    await state.set_state(BookingStates.waiting_for_price_change_start_date)
    await show_price_calendar(message, state, mode="start")
    logging.info("Админ %s ввёл новую цену: %s", message.from_user.id, new_price)


@router.callback_query(
    F.data.startswith("price_month_"),
    StateFilter(
        BookingStates.waiting_for_price_change_start_date,
        BookingStates.waiting_for_price_change_end_date,
    ),
)
async def paginate_price_calendar(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет доступа к этой команде.", show_alert=True)
        return

    _, _, mode, year, month = callback.data.split("_")
    await show_price_calendar(callback, state, mode=mode, year=int(year), month=int(month))
    await callback.answer()


@router.callback_query(
    F.data.startswith("price_start_"),
    StateFilter(BookingStates.waiting_for_price_change_start_date),
)
async def select_price_start_date(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет доступа к этой команде.", show_alert=True)
        return

    _, _, year, month, day = callback.data.split("_")
    start_date = date(int(year), int(month), int(day))
    await state.update_data(price_change_start_date=start_date)
    await state.set_state(BookingStates.waiting_for_price_change_end_date)
    await show_price_calendar(callback, state, mode="end", year=start_date.year, month=start_date.month)
    await callback.answer()
    logging.info("Админ %s выбрал дату начала цены: %s", callback.from_user.id, start_date)


@router.callback_query(
    F.data.startswith("price_end_"),
    StateFilter(BookingStates.waiting_for_price_change_end_date),
)
async def select_price_end_date(callback: CallbackQuery, state: FSMContext):
    if str(callback.from_user.id) != str(ADMIN_CHAT_ID):
        await callback.answer("У вас нет доступа к этой команде.", show_alert=True)
        return

    _, _, year, month, day = callback.data.split("_")
    end_date = date(int(year), int(month), int(day))
    data = await state.get_data()
    start_date = data.get("price_change_start_date")
    price = data.get("price_change_value")
    if not start_date or not price:
        await callback.answer("Сессия изменения цены устарела. Начните заново.", show_alert=True)
        await state.clear()
        return
    if end_date < start_date:
        await callback.answer("Дата окончания не может быть раньше даты начала.", show_alert=True)
        return

    apply_price_override(start_date, end_date, price)
    session.add(
        AdminLog(
            admin_id=callback.from_user.id,
            action=f"Изменил цену на {price}₽ с {start_date} по {end_date}",
        )
    )
    session.commit()
    await state.clear()
    await callback.message.edit_text(
        f"✅ Цена изменена на {price}₽ с {start_date.strftime('%d.%m.%Y')} "
        f"по {end_date.strftime('%d.%m.%Y')}.\n\n"
        "Эта цена будет применяться к каждому платному месту при бронировании.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⬅️ Админка", callback_data="admin")]
            ]
        ),
    )
    await callback.answer()
    logging.info(
        "Админ %s изменил цену на %s с %s по %s",
        callback.from_user.id,
        price,
        start_date,
        end_date,
    )
