import logging

from sqlalchemy import inspect, text

from .models import Base
from .session import engine

MISSING_COLUMNS = {
    "bookings": {
        "children_beds": "VARCHAR",
        "payment_deadline": "DATETIME",
        "admin_comment": "VARCHAR",
        "manual_total": "INTEGER",
        "paid_amount": "INTEGER",
        "payment_method": "VARCHAR",
        "prepayment_type": "VARCHAR DEFAULT 'percent'",
        "prepayment_value": "INTEGER DEFAULT 50",
        "discount_amount": "INTEGER DEFAULT 0",
        "extra_services_amount": "INTEGER DEFAULT 0",
        "refund_amount": "INTEGER DEFAULT 0",
        "calculated_total": "INTEGER",
        "payment_status": "VARCHAR",
        "stay_status": "VARCHAR DEFAULT 'awaiting_checkin'",
        "stay_status_changed_at": "DATETIME",
    },
    "news": {
        "video_id": "VARCHAR",
    },
    "support_tickets": {
        "user_message_id": "INTEGER",
    },
}


def run_migrations():
    """Create tables and add known missing columns without touching data."""
    Base.metadata.create_all(engine)
    inspector = inspect(engine)

    with engine.begin() as connection:
        existing_tables = set(inspector.get_table_names())
        for table_name, columns in MISSING_COLUMNS.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {
                column["name"] for column in inspector.get_columns(table_name)
            }
            for column_name, column_type in columns.items():
                if column_name in existing_columns:
                    continue
                connection.execute(
                    text(
                        f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
                    )
                )
                logging.info("Migration added column %s.%s", table_name, column_name)

        if "bookings" in existing_tables:
            connection.execute(
                text(
                    "UPDATE bookings SET calculated_total = manual_total "
                    "WHERE calculated_total IS NULL AND manual_total IS NOT NULL"
                )
            )
            connection.execute(
                text(
                    "UPDATE bookings SET stay_status = 'awaiting_checkin' "
                    "WHERE stay_status IS NULL"
                )
            )
