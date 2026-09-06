"""SQLite guards also protect writes from older Telegram handlers."""

from sqlalchemy import text

from config import ACTIVE_BOOKING_STATUSES


def install_inventory_guards(connection):
    statuses = ",".join(f"'{status}'" for status in ACTIVE_BOOKING_STATUSES)

    def shared_parts(alias):
        parts = [
            f"(instr('+' || NEW.room_type || '+', '+{part}+') > 0 "
            f"AND instr('+' || {alias}.room_type || '+', '+{part}+') > 0)"
            for part in ("2", "5", "4", "6")
        ]
        return f"(NEW.room_type = 'all' OR {alias}.room_type IN ('all', 'all_rooms') OR {' OR '.join(parts)})"

    for name, operation in (
        ("insert", "INSERT"),
        ("update", "UPDATE OF room_type, date_from, date_to, status, deleted_at"),
    ):
        connection.execute(text(f"""
            CREATE TRIGGER IF NOT EXISTS booking_inventory_{name}
            BEFORE {operation} ON bookings
            WHEN NEW.deleted_at IS NULL AND NEW.status IN ({statuses})
            BEGIN
                SELECT RAISE(ABORT, 'booking_inventory_conflict') WHERE EXISTS (
                    SELECT 1 FROM bookings b
                    WHERE b.id != COALESCE(NEW.id, -1) AND b.deleted_at IS NULL
                    AND b.status IN ({statuses})
                    AND b.date_from < NEW.date_to AND b.date_to > NEW.date_from
                    AND {shared_parts('b')}
                );
                SELECT RAISE(ABORT, 'booking_blocked_date') WHERE EXISTS (
                    SELECT 1 FROM blocked_dates b
                    WHERE b.date >= NEW.date_from AND b.date < NEW.date_to
                    AND {shared_parts('b')}
                );
            END
        """))
    connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_booking_inventory ON bookings "
        "(status, date_from, date_to)"
    ))
