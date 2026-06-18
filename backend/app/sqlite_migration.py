from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import Base
from app.models import Garment, Outfit, UploadSession, User, WeatherCache


@dataclass(frozen=True)
class MigrationResult:
    users_created: int = 0
    users_merged: int = 0
    upload_sessions: int = 0
    garments: int = 0
    outfits: int = 0
    weather_cache: int = 0


def migrate_sqlite_to_database(source_url: str, target_url: str | None = None) -> MigrationResult:
    if not source_url.startswith("sqlite"):
        raise ValueError("source_url must be a sqlite database URL")

    target_url = target_url or get_settings().database_url
    source_engine = create_engine(source_url, connect_args={"check_same_thread": False})
    target_engine = create_engine(target_url)
    Base.metadata.create_all(target_engine)

    with Session(source_engine) as source, Session(target_engine) as target:
        user_id_map: dict[str, str] = {}
        users_created = 0
        users_merged = 0

        for legacy_user in source.execute(select(User)).scalars():
            existing = target.execute(select(User).where(User.email == legacy_user.email)).scalar_one_or_none()
            if existing is not None:
                user_id_map[legacy_user.id] = existing.id
                users_merged += 1
                continue

            next_id = legacy_user.id if target.get(User, legacy_user.id) is None else str(uuid4())
            user_id_map[legacy_user.id] = next_id
            target.add(User(**{**_columns(legacy_user), "id": next_id}))
            users_created += 1

        target.flush()
        upload_sessions = _copy_owned_rows(source, target, UploadSession, user_id_map)
        garments = _copy_garments(source, target, user_id_map)
        outfits = _copy_owned_rows(source, target, Outfit, user_id_map)
        weather_cache = _copy_owned_rows(source, target, WeatherCache, user_id_map)
        target.commit()

    return MigrationResult(
        users_created=users_created,
        users_merged=users_merged,
        upload_sessions=upload_sessions,
        garments=garments,
        outfits=outfits,
        weather_cache=weather_cache,
    )


def migrate_legacy_sqlite_file(path: str | Path = "aiwardrobe.db") -> MigrationResult:
    db_path = Path(path)
    if not db_path.is_absolute():
        db_path = Path.cwd() / db_path
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    return migrate_sqlite_to_database(f"sqlite:///{db_path}")


def _copy_owned_rows(
    source: Session,
    target: Session,
    model: type[UploadSession] | type[Outfit] | type[WeatherCache],
    user_id_map: dict[str, str],
) -> int:
    inserted = 0
    for row in source.execute(select(model)).scalars():
        if target.get(model, row.id) is not None:
            continue
        mapped_user_id = user_id_map.get(row.user_id)
        if mapped_user_id is None:
            continue
        target.add(model(**{**_columns(row), "user_id": mapped_user_id}))
        inserted += 1
    target.flush()
    return inserted


def _copy_garments(source: Session, target: Session, user_id_map: dict[str, str]) -> int:
    inserted = 0
    for garment in source.execute(select(Garment)).scalars():
        if target.get(Garment, garment.id) is not None:
            continue
        mapped_user_id = user_id_map.get(garment.user_id)
        if mapped_user_id is None:
            continue
        source_upload_id = garment.source_upload_id
        if source_upload_id and target.get(UploadSession, source_upload_id) is None:
            source_upload_id = None
        target.add(Garment(**{**_columns(garment), "user_id": mapped_user_id, "source_upload_id": source_upload_id}))
        inserted += 1
    target.flush()
    return inserted


def _columns(row: object) -> dict[str, object]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate legacy AiWardrobe SQLite data into the configured database.")
    parser.add_argument("--source", default="aiwardrobe.db", help="Path to the legacy SQLite database file.")
    args = parser.parse_args()
    result = migrate_legacy_sqlite_file(args.source)
    print(result)


if __name__ == "__main__":
    main()
