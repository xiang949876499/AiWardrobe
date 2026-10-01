from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _connect_args(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_database_url: str | None = None


def get_engine() -> Engine:
    global _database_url, _engine, _session_factory
    settings = get_settings()
    if _engine is None or _database_url != settings.database_url:
        _database_url = settings.database_url
        _engine = create_engine(settings.database_url, connect_args=_connect_args(settings.database_url))
        _session_factory = sessionmaker(bind=_engine, autoflush=False, autocommit=False)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


def reset_database_connection() -> None:
    global _database_url, _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _database_url = None
    _engine = None
    _session_factory = None


def init_db() -> None:
    engine = get_engine()
    from app.migrations import require_current_schema, upgrade_database

    settings = get_settings()
    if settings.environment == "production" and not settings.testing:
        require_current_schema(engine)
    else:
        upgrade_database(engine)


def get_db() -> Generator[Session, None, None]:
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
