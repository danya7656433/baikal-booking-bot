import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DATA_DIR = Path(os.getenv("DATA_DIR", ".")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", DATA_DIR / "booking.db")).resolve()
BACKUP_DIR = Path(os.getenv("BACKUP_DIR", DATA_DIR / "backups")).resolve()
REPORT_DIR = Path(os.getenv("REPORT_DIR", DATA_DIR / "reports")).resolve()
LOG_PATH = Path(os.getenv("LOG_PATH", DATA_DIR / "bot.log")).resolve()
LOCK_PATH = Path(os.getenv("LOCK_PATH", DATA_DIR / "bot.lock")).resolve()

for directory in (DATABASE_PATH.parent, BACKUP_DIR, REPORT_DIR, LOG_PATH.parent, LOCK_PATH.parent):
    directory.mkdir(parents=True, exist_ok=True)
