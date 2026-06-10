import os
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from config import ACTIVE_BOOKING_STATUSES, BookingStatus
from database import Booking, SupportTicket, session
from services.backup_service import list_database_backups
from services.paths import DATABASE_PATH, LOG_PATH


def build_health_report() -> str:
    session.execute(text("SELECT 1"))
    active = session.query(Booking).filter(Booking.status.in_(ACTIVE_BOOKING_STATUSES)).count()
    paid = session.query(Booking).filter(Booking.status == BookingStatus.PAID.value).count()
    support = session.query(SupportTicket).filter(SupportTicket.status == "open").count()
    errors = 0
    if LOG_PATH.exists():
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as log_file:
            errors = sum("ERROR" in line or "CRITICAL" in line for line in log_file)
    backups = list_database_backups()
    backup_text = "нет"
    if backups:
        modified = datetime.fromtimestamp(backups[0].stat().st_mtime)
        backup_text = modified.strftime("%d.%m.%Y %H:%M")
    db_size = DATABASE_PATH.stat().st_size / 1024 / 1024 if DATABASE_PATH.exists() else 0
    free_gb = shutil.disk_usage(".").free / 1024 ** 3
    return (
        "🩺 Состояние бота\n\n"
        "✅ Бот: работает\n✅ База данных: работает\n"
        f"📦 Размер базы: {db_size:.2f} МБ\n"
        f"💾 Свободно на диске: {free_gb:.1f} ГБ\n"
        f"📋 Активных заявок: {active}\n"
        f"💚 Оплаченных заявок: {paid}\n"
        f"💬 Открытых обращений: {support}\n"
        f"🗄 Последний бэкап: {backup_text}\n"
        f"⚠️ Ошибок в логе: {errors}"
    )
