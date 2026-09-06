import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from db import (
    AdminLog,
    Base,
    BlockedDate,
    Booking,
    BookingChange,
    BookingDraft,
    BookingHistory,
    BookingSeason,
    BookingYear,
    ErrorEvent,
    GuestNote,
    News,
    NotificationLog,
    PaymentTransaction,
    Review,
    Room,
    RoomPriceOverride,
    Session,
    SupportTicket,
    SupportMessage,
    UpdateLog,
    User,
    engine,
    get_session,
    session,
)
from db.migrations import run_migrations


def init_settings():
    run_migrations()
    try:
        default_rooms = [
            (
                "2-местный",
                2,
                3000,
                "✔️Двуспальная кровать",
            ),
            (
                "4-местный",
                4,
                6000,
                "Номер на 4 человека с выходом на балкон и мансарду.\n"
                "✔️Двуспальная кровать\n"
                "✔️2 односпальные кровати",
            ),
            (
                "6-местный",
                6,
                9000,
                "✔️Двуспальная кровать\n✔️4 односпальных места",
            ),
            (
                "Домик на 2 места",
                2,
                3000,
                "Отдельный домик на 2 человека.",
            ),
        ]
        for name, capacity, price, description in default_rooms:
            if not session.query(Room).filter_by(name=name).first():
                session.add(
                    Room(
                        name=name,
                        capacity=capacity,
                        price=price,
                        description=description,
                    )
                )
        session.commit()

        if not session.query(BookingYear).first():
            year = datetime.now(ZoneInfo("Asia/Irkutsk")).year
            session.add(BookingYear(year=year))
            session.commit()
            logging.info("Booking year initialized: %s", year)

        if not session.query(BookingSeason).first():
            session.add(BookingSeason(is_active=True))
            session.commit()
            logging.info("Booking season initialized: active")
    except Exception:
        session.rollback()
        logging.exception("Failed to initialize settings")
        raise


__all__ = [
    "AdminLog",
    "Base",
    "BlockedDate",
    "Booking",
    "BookingChange",
    "BookingDraft",
    "BookingHistory",
    "BookingSeason",
    "BookingYear",
    "ErrorEvent",
    "GuestNote",
    "News",
    "NotificationLog",
    "PaymentTransaction",
    "Review",
    "Room",
    "RoomPriceOverride",
    "Session",
    "SupportTicket",
    "SupportMessage",
    "UpdateLog",
    "User",
    "engine",
    "get_session",
    "init_settings",
    "session",
]
