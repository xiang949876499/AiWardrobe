from collections.abc import Generator
from contextlib import nullcontext

from sqlalchemy import create_engine
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _connect_args(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    options = str(make_url(database_url).query.get("options", ""))
    return {"connect_timeout": 3, "options": options + " -c statement_timeout=5000 -c lock_timeout=2000"}


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_database_url: str | None = None


def get_engine() -> Engine:
    global _database_url, _engine, _session_factory
    settings = get_settings()
    if _engine is None or _database_url != settings.database_url:
        _database_url = settings.database_url
        _engine = create_engine(settings.database_url, connect_args=_connect_args(settings.database_url), pool_timeout=3)
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
    from app.migrations import upgrade, verify
    if get_settings().environment == "production":
        verify(get_engine())
    else:
        upgrade(get_engine())


def _ensure_lightweight_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "users" not in tables:
        return
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    with (engine.begin() if isinstance(engine, Engine) else nullcontext(engine)) as connection:
        if "password_hash" not in user_columns:
            connection.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR(128)"))
        if "garments" in tables:
            garment_columns = {column["name"] for column in inspector.get_columns("garments")}
            for name, ddl in {
                "source_upload_id": "VARCHAR(36)",
                "crop_box": "JSON",
                "review_status": "VARCHAR(32) DEFAULT 'confirmed'",
            }.items():
                if name not in garment_columns:
                    connection.execute(text(f"ALTER TABLE garments ADD COLUMN {name} {ddl}"))
        if "outfits" in tables:
            outfit_columns = {column["name"] for column in inspector.get_columns("outfits")}
            for name, ddl in {
                "name": "VARCHAR(160) DEFAULT ''",
                "source": "VARCHAR(32) DEFAULT 'ai'",
                "is_fixed": "BOOLEAN DEFAULT 0",
                "weather_snapshot": "JSON",
            }.items():
                if name not in outfit_columns:
                    connection.execute(text(f"ALTER TABLE outfits ADD COLUMN {name} {ddl}"))


def _ensure_wedding_project_columns(engine: Engine) -> None:
    """Keep early SQLite/Postgres wedding prototypes compatible with create_all.

    create_all adds the new wedding tables. This additive check covers only the
    small set of project counters that may be missing from an older prototype
    table; it never rebuilds or drops user data.
    """
    inspector = inspect(engine)
    if "wedding_projects" not in set(inspector.get_table_names()):
        return
    columns = {column["name"] for column in inspector.get_columns("wedding_projects")}
    missing = {
        "current_version": "INTEGER NOT NULL DEFAULT 1",
        "frame_approved_version": "INTEGER",
        "budget_limit_fen": "INTEGER NOT NULL DEFAULT 15000",
        "budget_delivery_reserve_fen": "INTEGER NOT NULL DEFAULT 2000",
        "budget_reserved_fen": "INTEGER NOT NULL DEFAULT 0",
        "budget_spent_fen": "INTEGER NOT NULL DEFAULT 0",
    }
    with (engine.begin() if isinstance(engine, Engine) else nullcontext(engine)) as connection:
        for name, ddl in missing.items():
            if name not in columns:
                connection.execute(text(f"ALTER TABLE wedding_projects ADD COLUMN {name} {ddl}"))
        if "wedding_consent_invites" in set(inspect(connection).get_table_names()):
            invite_columns = {column["name"] for column in inspect(connection).get_columns("wedding_consent_invites")}
            if "status" not in invite_columns:
                connection.execute(
                    text(
                        "ALTER TABLE wedding_consent_invites "
                        "ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'active'"
                    )
                )


def get_db() -> Generator[Session, None, None]:
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
