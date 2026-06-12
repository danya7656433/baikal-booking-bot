from contextlib import contextmanager


def session_context(Session):
    @contextmanager
    def get_test_session():
        db_session = Session()
        try:
            yield db_session
            db_session.commit()
        except Exception:
            db_session.rollback()
            raise
        finally:
            db_session.close()
    return get_test_session
