import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text

from db.migrations import run_migrations


class ReliableCoreMigrationsTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = Path(self.temp_dir.name) / "legacy.db"
        self.engine = create_engine(f"sqlite:///{database_path.as_posix()}")
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    """
                    CREATE TABLE bookings (
                        id INTEGER PRIMARY KEY,
                        manual_total INTEGER,
                        paid_amount INTEGER
                    )
                    """
                )
            )
            connection.execute(
                text(
                    "INSERT INTO bookings (id, manual_total, paid_amount) "
                    "VALUES (1, 22500, 10000)"
                )
            )

    def tearDown(self):
        self.engine.dispose()
        self.temp_dir.cleanup()

    def test_migration_adds_reliable_core_schema_and_preserves_booking(self):
        with patch("db.migrations.engine", self.engine):
            run_migrations()

        inspector = inspect(self.engine)
        booking_columns = {
            column["name"] for column in inspector.get_columns("bookings")
        }
        self.assertTrue(
            {
                "calculated_total",
                "payment_status",
                "stay_status",
                "stay_status_changed_at",
            }.issubset(booking_columns)
        )
        self.assertTrue(
            {"payment_transactions", "booking_changes", "error_events"}.issubset(
                inspector.get_table_names()
            )
        )
        self.assertEqual(
            {
                column["name"]
                for column in inspector.get_columns("payment_transactions")
            },
            {
                "id",
                "booking_id",
                "amount",
                "kind",
                "payment_method",
                "admin_id",
                "comment",
                "created_at",
            },
        )
        self.assertEqual(
            {column["name"] for column in inspector.get_columns("booking_changes")},
            {
                "id",
                "booking_id",
                "actor_id",
                "kind",
                "before_json",
                "after_json",
                "created_at",
            },
        )
        self.assertEqual(
            {column["name"] for column in inspector.get_columns("error_events")},
            {
                "id",
                "fingerprint",
                "message",
                "traceback_preview",
                "first_seen_at",
                "last_seen_at",
                "total_count",
                "reported_count",
                "last_summary_at",
            },
        )

        with self.engine.connect() as connection:
            booking = connection.execute(
                text(
                    "SELECT manual_total, paid_amount, calculated_total, "
                    "payment_status, stay_status FROM bookings WHERE id = 1"
                )
            ).mappings().one()

        self.assertEqual(booking["manual_total"], 22500)
        self.assertEqual(booking["paid_amount"], 10000)
        self.assertEqual(booking["calculated_total"], 22500)
        self.assertIsNone(booking["payment_status"])
        self.assertEqual(booking["stay_status"], "awaiting_checkin")

    def test_migration_is_idempotent(self):
        with patch("db.migrations.engine", self.engine):
            run_migrations()
            run_migrations()

        with self.engine.connect() as connection:
            count = connection.execute(
                text("SELECT COUNT(*) FROM bookings")
            ).scalar_one()
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
