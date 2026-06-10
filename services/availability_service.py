from __future__ import annotations

from datetime import date, timedelta

from utils import get_booked_dates, get_optimal_room_combinations, is_room_available


async def get_available_room_options(people: int, duration: int) -> list[str]:
    return await get_optimal_room_combinations(people, duration=duration)


async def is_available_for_duration(
    room_type: str,
    start_date: date,
    duration: int,
) -> bool:
    occupied_dates, checkin_dates = await get_booked_dates(room_type)
    end_date = start_date + timedelta(days=max(duration, 1) - 1)
    return await is_room_available(
        room_type,
        start_date,
        end_date,
        [day.isoformat() for day in occupied_dates],
        [day.isoformat() for day in checkin_dates],
    )
