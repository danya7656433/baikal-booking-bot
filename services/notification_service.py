import asyncio
import logging
import time
import traceback

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from config import ADMIN_CHAT_ID


class TelegramHandler(logging.Handler):
    def __init__(self, bot: Bot, level=logging.NOTSET, rate_limit_seconds=60):
        super().__init__(level)
        self.bot = bot
        self.rate_limit_seconds = rate_limit_seconds
        self.last_sent = 0
        self.last_message = ""
        self._sending = False

    def emit(self, record):
        try:
            if self._sending:
                return
            log_message = self.format(record)
            current_time = time.time()
            if (
                log_message == self.last_message
                and current_time - self.last_sent < self.rate_limit_seconds
            ):
                return
            if len(log_message) > 4096:
                log_message = log_message[:4000] + "... (truncated)"
            self._sending = True
            asyncio.create_task(
                self._send(log_message)
            )
            self.last_sent = current_time
            self.last_message = log_message
        except Exception:
            self._sending = False

    async def _send(self, log_message: str):
        try:
            await self.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"🚨 Критическая ошибка бота\n\n{log_message}",
            )
        except TelegramAPIError:
            pass
        finally:
            self._sending = False
