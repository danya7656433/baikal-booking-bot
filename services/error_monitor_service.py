import asyncio
import hashlib
from datetime import datetime, timedelta

from config import ADMIN_CHAT_ID
from db import ErrorEvent, get_session


def make_error_fingerprint(exc: BaseException, traceback_text: str) -> str:
    tail = "\n".join((traceback_text or "").splitlines()[-4:])
    raw = f"{type(exc).__name__}:{exc}:{tail}"
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()


def record_error(exc: BaseException, traceback_text: str) -> dict:
    fingerprint = make_error_fingerprint(exc, traceback_text)
    with get_session() as db_session:
        event = db_session.query(ErrorEvent).filter_by(fingerprint=fingerprint).first()
        first = event is None
        if first:
            event = ErrorEvent(
                fingerprint=fingerprint,
                message=f"{type(exc).__name__}: {exc}",
                traceback_preview=(traceback_text or "")[-2000:],
                total_count=1,
                reported_count=0,
            )
            db_session.add(event)
        else:
            event.total_count += 1
            event.last_seen_at = datetime.now()
        return {"first": first, "fingerprint": fingerprint, "message": event.message}


async def flush_error_summaries(bot) -> None:
    while True:
        cutoff = datetime.now() - timedelta(minutes=30)
        with get_session() as db_session:
            events = (
                db_session.query(ErrorEvent)
                .filter(ErrorEvent.total_count > ErrorEvent.reported_count, ErrorEvent.first_seen_at <= cutoff)
                .all()
            )
            summaries = [(e.id, e.message, e.total_count - e.reported_count) for e in events]
        for event_id, message, repeats in summaries:
            await bot.send_message(ADMIN_CHAT_ID, f"🚨 Повторяющаяся ошибка\n{message}\nПовторов за период: {repeats}")
            with get_session() as db_session:
                event = db_session.get(ErrorEvent, event_id)
                event.reported_count = event.total_count
                event.last_summary_at = datetime.now()
        await asyncio.sleep(60)
