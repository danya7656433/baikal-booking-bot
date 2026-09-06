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
    with get_session() as db_session:
        return calculate_revenue_in_session(db_session, room_type, total_people, date_from, date_to)


def calculate_revenue_in_session(db_session, room_type, total_people, date_from, date_to) -> int:
    total = 0
    current_date = date_from
    overrides = {
        item.date: item.price for item in db_session.query(RoomPriceOverride).filter(
            RoomPriceOverride.date >= date_from, RoomPriceOverride.date < date_to,
        ).order_by(RoomPriceOverride.id)
    }
    while current_date < date_to:
        price_per_person = overrides.get(current_date, PRICE_PER_ADULT)
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
