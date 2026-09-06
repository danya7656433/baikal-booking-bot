import os
import sqlite3
from datetime import datetime
from pathlib import Path

from services.paths import BACKUP_DIR, DATABASE_PATH


def create_database_backup(
    source: str | None = None,
    destination_dir: str | None = None,
    timestamped: bool = False,
) -> str:
    source = source or str(DATABASE_PATH)
    destination_dir = destination_dir or str(BACKUP_DIR)
    Path(destination_dir).mkdir(parents=True, exist_ok=True)
    if timestamped:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        destination = os.path.join(destination_dir, f"backup_{stamp}.db")
    else:
        destination = os.path.join(destination_dir, "backup.db")

    source_path = Path(source).resolve()
    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if source_path == Path(destination).resolve():
        raise ValueError("Source and backup destination must differ")
    source_connection = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    try:
        destination_connection = sqlite3.connect(destination)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
    finally:
        source_connection.close()
    return destination


async def backup_database() -> str:
    return create_database_backup(timestamped=True)


def list_database_backups(destination_dir: str | None = None) -> list[Path]:
    destination_dir = destination_dir or str(BACKUP_DIR)
    backup_dir = Path(destination_dir)
    if not backup_dir.exists():
        return []
    return sorted(
        backup_dir.glob("backup_*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def restore_database_backup(
    backup_path: str,
    destination: str | None = None,
    create_safety_copy: bool = True,
) -> str:
    source = Path(backup_path).resolve()
    destination_path = Path(destination or DATABASE_PATH).resolve()
    if not source.is_file() or source.suffix.lower() != ".db":
        raise ValueError("Выбранная резервная копия не найдена.")
    if create_safety_copy and destination_path.exists():
        create_database_backup(
            source=str(destination_path),
            destination_dir=str(source.parent),
            timestamped=True,
        )
    source_connection = sqlite3.connect(source)
    try:
        destination_connection = sqlite3.connect(destination_path)
        try:
            source_connection.backup(destination_connection)
        finally:
            destination_connection.close()
    finally:
        source_connection.close()
    return str(destination_path)
