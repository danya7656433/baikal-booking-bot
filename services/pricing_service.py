import logging
from datetime import date, timedelta

from config import ALL_ROOMS_PRICE, PRICE_PER_ADULT
from db import RoomPriceOverride, get_session


async def calculate_revenue(
    room_type: str,
    total_people: int,
    date_from: date,
    date_to: date,
) -> int:
    total = 0
    current_date = date_from

    with get_session() as db_session:
        while current_date < date_to:
            price_override = (
                db_session.query(RoomPriceOverride).filter_by(date=current_date).first()
            )
            price_per_person = (
                price_override.price if price_override else PRICE_PER_ADULT
            )

            if room_type == "all" and 8 <= total_people <= 10:
                total += ALL_ROOMS_PRICE
            else:
                total += total_people * price_per_person

            current_date += timedelta(days=1)

    logging.info(
        "Calculated revenue: room=%s people=%s period=%s..%s total=%s",
        room_type,
        total_people,
        date_from,
        date_to,
        total,
    )
    return total
