from sqlalchemy import text

from app.db.session import make_engine, make_session_factory


def test_session_can_connect_to_test_database(test_database_url: str) -> None:
    engine = make_engine(test_database_url)
    try:
        session_factory = make_session_factory(engine)
        with session_factory() as session:
            assert session.execute(text("SELECT 1")).scalar_one() == 1
    finally:
        engine.dispose()
