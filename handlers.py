import asyncio
import logging

from aiogram import Bot, Dispatcher

from bot.routers.admin import router as admin_router
from bot.routers.admin_news import router as admin_news_router
from bot.routers.admin_prices import router as admin_prices_router
from bot.routers.admin_payments import router as admin_payments_router
from bot.routers.admin_booking_edit import router as admin_booking_edit_router
from bot.routers.admin_tools import router as admin_tools_router
from bot.routers.legacy import check_payment_deadlines, router as legacy_router
from bot.routers.my_bookings import router as my_bookings_router


def register_handlers(dp: Dispatcher, bot: Bot):
    logging.info("Регистрация обработчиков начата")
    dp.include_router(admin_router)
    dp.include_router(admin_prices_router)
    dp.include_router(admin_news_router)
    dp.include_router(admin_tools_router)
    dp.include_router(admin_payments_router)
    dp.include_router(admin_booking_edit_router)
    dp.include_router(my_bookings_router)
    dp.include_router(legacy_router)
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(check_payment_deadlines(bot))
        logging.info("Задача check_payment_deadlines успешно зарегистрирована")
    except Exception as e:
        logging.error("Ошибка при регистрации задач: %s", str(e))
        raise
    logging.info("Регистрация обработчиков завершена")
