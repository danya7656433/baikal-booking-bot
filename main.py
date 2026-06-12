import asyncio
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from aiogram import Bot, Dispatcher
from dotenv import load_dotenv

from database import init_settings
from db.migrations import run_migrations
from handlers import register_handlers
from services.backup_service import backup_database
from services.notification_service import TelegramHandler
from services.paths import LOCK_PATH, LOG_PATH

load_dotenv()

file_handler = RotatingFileHandler(
    filename=LOG_PATH,
    maxBytes=10 * 1024 * 1024,
    backupCount=5,
    encoding="utf-8",
)
file_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
    )
)
logging.basicConfig(handlers=[file_handler], level=logging.INFO, encoding="utf-8")

_instance_lock_file = None


def acquire_instance_lock() -> bool:
    global _instance_lock_file
    lock_path = LOCK_PATH
    _instance_lock_file = open(lock_path, "a+", encoding="utf-8")
    try:
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(_instance_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(_instance_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        logging.error("Another bot instance is already running")
        return False
    _instance_lock_file.seek(0)
    _instance_lock_file.truncate()
    _instance_lock_file.write(str(os.getpid()))
    _instance_lock_file.flush()
    return True


async def schedule_backups():
    logging.info("Background backup task started")
    while True:
        try:
            backup_file = await backup_database()
            logging.info("Database backup created: %s", backup_file)
        except Exception:
            logging.exception("Backup task failed")
        await asyncio.sleep(21600)


async def main():
    run_migrations()
    init_settings()

    bot = Bot(token=os.getenv("BOT_TOKEN"))
    dp = Dispatcher()

    telegram_handler = TelegramHandler(bot=bot, level=logging.ERROR, rate_limit_seconds=300)
    telegram_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s - %(levelname)s - %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
    )
    logging.getLogger().addHandler(telegram_handler)
    logging.info("Telegram log handler enabled")
    loop = asyncio.get_running_loop()

    def report_async_exception(_loop, context):
        exception = context.get("exception")
        if exception:
            logging.error("Unhandled async exception", exc_info=exception)
        else:
            logging.error("Unhandled async exception: %s", context.get("message"))

    loop.set_exception_handler(report_async_exception)

    register_handlers(dp, bot)

    try:
        from utils import check_support_timeouts, schedule_reminders, schedule_reviews
        from services.stay_service import schedule_stay_transitions

        asyncio.create_task(schedule_reminders(bot))
        asyncio.create_task(schedule_backups())
        asyncio.create_task(schedule_reviews(bot))
        asyncio.create_task(check_support_timeouts(bot))
        asyncio.create_task(schedule_stay_transitions())
        logging.info("Background tasks registered")
    except ImportError:
        logging.exception("Failed to register background tasks")

    try:
        await dp.start_polling(bot)
    except Exception:
        logging.exception("Bot polling failed")


if __name__ == "__main__":
    try:
        if not acquire_instance_lock():
            sys.exit(1)
        asyncio.run(main())
    except Exception:
        logging.exception("Application crashed")
