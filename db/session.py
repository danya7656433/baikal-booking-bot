from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from services.paths import DATABASE_PATH

engine = create_engine(
    f"sqlite:///{DATABASE_PATH.as_posix()}", connect_args={"timeout": 15},
)
Session = sessionmaker(bind=engine)

# Backward-compatible global session. New code should prefer get_session().
session = Session()


@contextmanager
def get_session():
    db_session = Session()
    try:
        yield db_session
        db_session.commit()
    except Exception:
        db_session.rollback()
        raise
    finally:
        db_session.close()
