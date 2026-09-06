import asyncio
import logging
from datetime import datetime, timedelta

from db import get_session
from webapp.models import Outbox


async def schedule_web_notifications(bot):
    while True:
        try:
            with get_session() as session:
                pending = session.query(Outbox).filter(Outbox.sent_at.is_(None), Outbox.next_attempt_at <= datetime.utcnow()).order_by(Outbox.id).limit(20).all()
                messages = [(row.id, row.chat_id, row.text) for row in pending]
            for row_id, chat_id, text in messages:
                try:
                    await bot.send_message(chat_id, text)
                except Exception:
                    logging.exception("Web notification %s delivery failed", row_id)
                    with get_session() as session:
                        row = session.get(Outbox, row_id)
                        row.attempts += 1
                        row.next_attempt_at = datetime.utcnow() + timedelta(seconds=min(3600, 30 * 2 ** min(row.attempts, 7)))
                else:
                    with get_session() as session:
                        session.get(Outbox, row_id).sent_at = datetime.utcnow()
        except Exception:
            logging.exception("Web notification worker failed")
        await asyncio.sleep(15)
