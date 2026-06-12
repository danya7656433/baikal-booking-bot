import logging
import re
import asyncio
import os
import sys
from datetime import date

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import FSInputFile, KeyboardButton, Message, ReplyKeyboardMarkup

from bot.keyboards import (
    BTN_ADMIN,
    BTN_ADMIN_BACKUPS,
    BTN_ADMIN_EXPORTS,
    BTN_ADMIN_FINANCE,
    BTN_ADMIN_HEALTH,
    BTN_ADMIN_RESTART,
    admin_menu_keyboard,
)
from bot.states import BookingStates
from config import ADMIN_CHAT_ID, BookingStatus
from database import AdminLog, Booking, BookingYear, User, engine, get_session, session
from services.backup_service import (
    create_database_backup,
    list_database_backups,
    restore_database_backup,
)
from services.booking_service import get_booking_people_count
from services.financial_service import calculate_required_prepayment
from services.payment_service import add_adjustment, add_refund, get_payment_balance
from services.health_service import build_health_report
from services.pricing_service import calculate_revenue
from services.report_service import (
    create_contacts_excel,
    create_financial_excel,
    create_financial_pdf,
)

router = Router()

EXPORT_EXCEL = "📊 Подробный Excel"
EXPORT_PDF = "📄 Финансовый PDF"
EXPORT_CONTACTS = "👥 Контакты Excel"
BACKUP_CREATE = "➕ Создать копию"
BACKUP_DOWNLOAD = "⬇️ Скачать последнюю"
BACKUP_RESTORE = "♻️ Восстановить последнюю"


def _admin(user_id: int) -> bool:
    return str(user_id) == str(ADMIN_CHAT_ID)


def _submenu(*buttons: str) -> ReplyKeyboardMarkup:
    rows = [[KeyboardButton(text=button)] for button in buttons]
    if BTN_ADMIN not in buttons:
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
    )


@router.message(F.text == BTN_ADMIN_FINANCE)
async def admin_finance_start(message: Message, state: FSMContext):
    if not _admin(message.from_user.id):
        return
    await state.set_state(BookingStates.waiting_for_admin_finance_update)
    await message.answer(
        "💰 Финансы заявки\n\n"
        "Отправьте одну команду:\n"
        "12 предоплата процент 30\n"
        "12 предоплата сумма 5000\n"
        "12 скидка 1000\n"
        "12 услуги 2500\n"
        "12 возврат 3000\n"
        "12 внесено 15000",
        reply_markup=_submenu(BTN_ADMIN),
    )


@router.message(BookingStates.waiting_for_admin_finance_update, F.text)
async def admin_finance_update(message: Message, state: FSMContext):
    if not _admin(message.from_user.id):
        return
    if message.text == BTN_ADMIN:
        await state.clear()
        await message.answer("👨‍💼 Панель администратора", reply_markup=admin_menu_keyboard())
        return
    parts = message.text.lower().split()
    if len(parts) < 3 or not parts[0].isdigit():
        await message.answer("Не понял команду. Пример: 12 скидка 1000")
        return
    booking_id = int(parts[0])
    action = parts[1]
    numbers = [int(value) for value in re.findall(r"\d+", " ".join(parts[2:]))]
    if not numbers:
        await message.answer("Укажите сумму или процент.")
        return
    value = numbers[-1]
    with get_session() as db_session:
        booking = db_session.get(Booking, booking_id)
        if not booking:
            await message.answer("Заявка не найдена.")
            return
        calculated = await calculate_revenue(
            booking.room_type,
            get_booking_people_count(booking),
            booking.date_from,
            booking.date_to,
        )
        booking.calculated_total = calculated
        current = get_payment_balance(db_session, booking_id)
        if action == "предоплата":
            booking.prepayment_type = "fixed" if "сумма" in parts else "percent"
            booking.prepayment_value = value
        elif action == "скидка":
            booking.discount_amount = value
        elif action == "услуги":
            booking.extra_services_amount = value
        elif action not in {"возврат", "внесено"}:
            await message.answer("Доступно: предоплата, скидка, услуги, возврат, внесено.")
            return
        booking_user_id = booking.user_id
        db_session.add(AdminLog(admin_id=message.from_user.id, booking_id=booking.id, action=f"Изменил финансы: {message.text}"))
    if action == "возврат":
        balance = add_refund(booking_id, value, "администратор", message.from_user.id, message.text)
    elif action == "внесено":
        difference = value - current["paid"]
        balance = add_adjustment(booking_id, difference, message.from_user.id, message.text) if difference else current
        with get_session() as db_session:
            booking = db_session.get(Booking, booking_id)
            booking.status = BookingStatus.PAID.value if balance["net_paid"] else booking.status
            booking.payment_deadline = None
    else:
        with get_session() as db_session:
            balance = get_payment_balance(db_session, booking_id)
    with get_session() as db_session:
        booking = db_session.get(Booking, booking_id)
        balance["required_prepayment"] = calculate_required_prepayment(booking, balance["total"])
    if action == "внесено" and booking_user_id:
        await message.bot.send_message(
            booking_user_id,
            f"💳 Оплата по заявке #{booking_id} обновлена.\n"
            f"✅ Внесено: {balance['paid']}₽\n"
            f"🧾 Осталось оплатить: {balance['remaining']}₽",
        )
    await message.answer(
        f"✅ Финансы заявки #{booking_id} обновлены\n\n"
        f"💰 Итого: {balance['total']}₽\n"
        f"🔐 Требуемая предоплата: {balance['required_prepayment']}₽\n"
        f"✅ Внесено: {balance['paid']}₽\n"
        f"↩️ Возвращено: {balance['refunds']}₽\n"
        f"🧾 Осталось: {balance['remaining']}₽",
        reply_markup=_submenu(BTN_ADMIN),
    )


@router.message(F.text == BTN_ADMIN_EXPORTS)
async def admin_exports_start(message: Message, state: FSMContext):
    if not _admin(message.from_user.id):
        return
    await state.set_state(BookingStates.waiting_for_admin_export_menu)
    await message.answer(
        "📤 Выберите отчет:",
        reply_markup=_submenu(EXPORT_EXCEL, EXPORT_PDF, EXPORT_CONTACTS),
    )


@router.message(BookingStates.waiting_for_admin_export_menu, F.text)
async def admin_export(message: Message, state: FSMContext):
    if not _admin(message.from_user.id):
        return
    if message.text == BTN_ADMIN:
        await state.clear()
        await message.answer("👨‍💼 Панель администратора", reply_markup=admin_menu_keyboard())
        return
    year_row = session.query(BookingYear).first()
    year = year_row.year if year_row else date.today().year
    bookings = session.query(Booking).filter(Booking.date_from >= date(year, 1, 1), Booking.date_from <= date(year, 12, 31)).all()
    try:
        if message.text == EXPORT_EXCEL:
            path = await create_financial_excel(bookings, year)
        elif message.text == EXPORT_PDF:
            path = await create_financial_pdf(bookings, year)
        elif message.text == EXPORT_CONTACTS:
            path = create_contacts_excel(session.query(User).all())
        else:
            await message.answer("Выберите отчет кнопкой снизу.")
            return
        await message.answer_document(FSInputFile(path), caption="✅ Отчет сформирован")
    except Exception:
        logging.exception("Failed to create admin export")
        await message.answer("Не удалось сформировать отчет. Ошибка отправлена администратору.")


@router.message(F.text == BTN_ADMIN_BACKUPS)
async def admin_backups_start(message: Message, state: FSMContext):
    if not _admin(message.from_user.id):
        return
    await state.set_state(BookingStates.waiting_for_admin_backup_menu)
    backups = list_database_backups()
    latest = backups[0].name if backups else "нет"
    await message.answer(
        f"🗄 Резервные копии\n\nПоследняя: {latest}",
        reply_markup=_submenu(BACKUP_CREATE, BACKUP_DOWNLOAD, BACKUP_RESTORE),
    )


@router.message(BookingStates.waiting_for_admin_backup_menu, F.text)
async def admin_backup_action(message: Message, state: FSMContext):
    if not _admin(message.from_user.id):
        return
    if message.text == BTN_ADMIN:
        await state.clear()
        await message.answer("👨‍💼 Панель администратора", reply_markup=admin_menu_keyboard())
        return
    if message.text == BACKUP_CREATE:
        path = create_database_backup(timestamped=True)
        await message.answer(f"✅ Создана копия: {path}")
        return
    backups = list_database_backups()
    if not backups:
        await message.answer("Резервных копий пока нет.")
        return
    if message.text == BACKUP_DOWNLOAD:
        await message.answer_document(FSInputFile(backups[0]), caption=f"🗄 {backups[0].name}")
        return
    if message.text == BACKUP_RESTORE:
        await state.update_data(restore_backup_path=str(backups[0]))
        await state.set_state(BookingStates.waiting_for_admin_backup_restore)
        await message.answer(
            f"⚠️ Будет восстановлена {backups[0].name}.\n"
            "Перед восстановлением автоматически создастся страховочная копия.\n\n"
            "Для подтверждения напишите: ВОССТАНОВИТЬ"
        )


@router.message(BookingStates.waiting_for_admin_backup_restore, F.text)
async def admin_backup_restore(message: Message, state: FSMContext):
    if not _admin(message.from_user.id):
        return
    if message.text.strip().upper() != "ВОССТАНОВИТЬ":
        await message.answer("Восстановление отменено.", reply_markup=admin_menu_keyboard())
        await state.clear()
        return
    data = await state.get_data()
    session.close()
    engine.dispose()
    restore_database_backup(data["restore_backup_path"])
    await state.clear()
    await message.answer(
        "✅ База восстановлена. Перезапустите бота, чтобы все процессы перечитали данные.",
        reply_markup=admin_menu_keyboard(),
    )


@router.message(F.text == BTN_ADMIN_HEALTH)
async def admin_health_button(message: Message):
    if not _admin(message.from_user.id):
        return
    try:
        await message.answer(build_health_report(), reply_markup=admin_menu_keyboard())
    except Exception:
        logging.exception("Health report failed")
        await message.answer("❌ Проверка состояния завершилась ошибкой.")


@router.message(F.text == BTN_ADMIN_RESTART)
async def admin_restart_start(message: Message, state: FSMContext):
    if not _admin(message.from_user.id):
        return
    await state.set_state(BookingStates.waiting_for_admin_restart_confirm)
    await message.answer(
        "⚠️ Перезапустить бота?\n\n"
        "На несколько секунд бот перестанет отвечать, затем запустится заново.",
        reply_markup=_submenu("✅ Подтвердить перезапуск", "❌ Отмена"),
    )


async def _restart_process(bot):
    await asyncio.sleep(2)
    logging.warning("Bot restart requested by administrator")
    await bot.session.close()
    os.execl(sys.executable, sys.executable, *sys.argv)


@router.message(BookingStates.waiting_for_admin_restart_confirm, F.text)
async def admin_restart_confirm(message: Message, state: FSMContext):
    if not _admin(message.from_user.id):
        return
    if message.text != "✅ Подтвердить перезапуск":
        await state.clear()
        await message.answer(
            "Перезапуск отменён.",
            reply_markup=admin_menu_keyboard(),
        )
        return
    session.add(AdminLog(admin_id=message.from_user.id, action="Запросил перезапуск бота"))
    session.commit()
    await state.clear()
    await message.answer("🔄 Перезапускаю бота. Подождите несколько секунд.")
    asyncio.create_task(_restart_process(message.bot))
