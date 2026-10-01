from __future__ import annotations

import argparse
from datetime import datetime, timezone

from sqlalchemy import Engine, inspect, text
from sqlalchemy.schema import CreateColumn

from app.database import Base, get_engine

LATEST_VERSION = 13


def current_version(engine: Engine) -> int:
    if "schema_migrations" not in inspect(engine).get_table_names():
        return 0
    with engine.connect() as connection:
        value = connection.execute(text("SELECT MAX(version) FROM schema_migrations")).scalar_one()
    return int(value or 0)


def upgrade_database(engine: Engine) -> int:
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, "
                "name VARCHAR(120) NOT NULL, "
                "applied_at VARCHAR(40) NOT NULL)"
            )
        )

    applied = current_version(engine)
    for version, name, migration in _MIGRATIONS:
        if version <= applied:
            continue
        migration(engine)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO schema_migrations (version, name, applied_at) "
                    "VALUES (:version, :name, :applied_at)"
                ),
                {
                    "version": version,
                    "name": name,
                    "applied_at": datetime.now(timezone.utc).isoformat(),
                },
            )
        applied = version
    return applied


def require_current_schema(engine: Engine) -> None:
    version = current_version(engine)
    if version != LATEST_VERSION:
        raise RuntimeError(
            f"Database schema is at version {version}; run `python -m app.migrations upgrade` "
            f"to apply version {LATEST_VERSION} before starting the application."
        )


def _baseline_and_legacy_columns(engine: Engine) -> None:
    from app import models  # noqa: F401

    _ensure_legacy_columns(engine)
    _add_missing_model_columns(engine)
    Base.metadata.create_all(bind=engine)


def _expand_analysis_and_task_schema(engine: Engine) -> None:
    from app import models  # noqa: F401

    _add_missing_model_columns(engine)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE garments SET review_status = 'legacy_unknown' "
                "WHERE review_status = 'confirmed' AND user_confirmed_at IS NULL "
                "AND analysis_source = 'legacy_unknown'"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_garments_source_candidate_id "
                "ON garments (source_candidate_id)"
            )
        )


def _persist_provider_results(engine: Engine) -> None:
    from app import models  # noqa: F401

    _add_missing_model_columns(engine)
    Base.metadata.create_all(bind=engine)


def _link_assets_to_domain_records(engine: Engine) -> None:
    from app import models  # noqa: F401

    _add_missing_model_columns(engine)
    Base.metadata.create_all(bind=engine)


def _add_rate_limit_counters(engine: Engine) -> None:
    from app import models  # noqa: F401

    _add_missing_model_columns(engine)
    Base.metadata.create_all(bind=engine)


def _add_provider_capacity_slots(engine: Engine) -> None:
    from app import models  # noqa: F401

    _add_missing_model_columns(engine)
    Base.metadata.create_all(bind=engine)


def _add_provider_slot_release_audit(engine: Engine) -> None:
    from app import models  # noqa: F401

    _add_missing_model_columns(engine)
    Base.metadata.create_all(bind=engine)


def _add_decision_feedback_and_wear_history(engine: Engine) -> None:
    from app import models  # noqa: F401

    _add_missing_model_columns(engine)
    Base.metadata.create_all(bind=engine)


def _add_asset_reclamation_state(engine: Engine) -> None:
    from app import models  # noqa: F401

    _add_missing_model_columns(engine)
    Base.metadata.create_all(bind=engine)


def _link_upload_session_assets(engine: Engine) -> None:
    from app import models  # noqa: F401

    _add_missing_model_columns(engine)
    Base.metadata.create_all(bind=engine)


def _backfill_legacy_candidate_decisions(engine: Engine) -> None:
    from app import models  # noqa: F401

    _add_missing_model_columns(engine)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE purchase_candidates SET decision_status = 'bought' "
            "WHERE status = 'saved' AND decision_status = 'considering'"
        ))
        connection.execute(text(
            "UPDATE purchase_candidates SET owned_garment_id = ("
            "SELECT garments.id FROM garments WHERE garments.source_candidate_id = purchase_candidates.id LIMIT 1) "
            "WHERE owned_garment_id IS NULL AND EXISTS ("
            "SELECT 1 FROM garments WHERE garments.source_candidate_id = purchase_candidates.id)"
        ))
        connection.execute(text(
            "UPDATE purchase_candidates SET ownership_state = 'linked' "
            "WHERE owned_garment_id IS NOT NULL AND ownership_state = 'never_saved'"
        ))
        connection.execute(text(
            "UPDATE purchase_candidates SET ownership_state = 'legacy_unlinked' "
            "WHERE status = 'saved' AND owned_garment_id IS NULL AND ownership_state = 'never_saved'"
        ))


def _add_task_stage_durations(engine: Engine) -> None:
    from app import models  # noqa: F401

    _add_missing_model_columns(engine)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(text(
            "UPDATE analysis_tasks SET stage_started_at = COALESCE(updated_at, created_at) "
            "WHERE stage_started_at IS NULL"
        ))
        connection.execute(text(
            "UPDATE analysis_tasks SET stage_durations_ms = '{}' WHERE stage_durations_ms IS NULL"
        ))


def _add_missing_model_columns(engine: Engine) -> None:
    from app import models  # noqa: F401

    existing_tables = set(inspect(engine).get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        existing_columns = {column["name"] for column in inspect(engine).get_columns(table.name)}
        missing = [column for column in table.columns if column.name not in existing_columns]
        for column in missing:
            if column.foreign_keys:
                raise RuntimeError(
                    f"Migration must explicitly add foreign-key column {table.name}.{column.name}"
                )
            ddl = str(CreateColumn(column).compile(dialect=engine.dialect))
            with engine.begin() as connection:
                connection.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {ddl}"))


def _ensure_legacy_columns(engine: Engine) -> None:
    legacy_columns: dict[str, dict[str, str]] = {
        "users": {"password_hash": "VARCHAR(128)"},
        "garments": {
            "source_upload_id": "VARCHAR(36)",
            "crop_box": "JSON",
            "review_status": "VARCHAR(32) DEFAULT 'confirmed'",
        },
        "outfits": {
            "name": "VARCHAR(160) DEFAULT ''",
            "source": "VARCHAR(32) DEFAULT 'ai'",
            "is_fixed": "BOOLEAN DEFAULT FALSE",
            "weather_snapshot": "JSON",
        },
    }
    for table_name, columns in legacy_columns.items():
        if table_name not in inspect(engine).get_table_names():
            continue
        present = {column["name"] for column in inspect(engine).get_columns(table_name)}
        missing = [(name, ddl) for name, ddl in columns.items() if name not in present]
        if not missing:
            continue
        with engine.begin() as connection:
            for name, ddl in missing:
                connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}"))


def _add_candidate_ownership_operations(engine: Engine) -> None:
    from app.models import CandidateOwnershipOperation

    CandidateOwnershipOperation.__table__.create(bind=engine, checkfirst=True)


_MIGRATIONS = (
    (1, "baseline_and_legacy_columns", _baseline_and_legacy_columns),
    (2, "reliable_analysis_and_persistent_tasks", _expand_analysis_and_task_schema),
    (3, "persist_provider_results_for_recovery", _persist_provider_results),
    (4, "link_private_assets_to_domain_records", _link_assets_to_domain_records),
    (5, "atomic_rate_limit_counters", _add_rate_limit_counters),
    (6, "provider_capacity_slots", _add_provider_capacity_slots),
    (7, "provider_slot_release_audit", _add_provider_slot_release_audit),
    (8, "decision_feedback_and_wear_history", _add_decision_feedback_and_wear_history),
    (9, "asset_reclamation_state", _add_asset_reclamation_state),
    (10, "link_upload_session_assets", _link_upload_session_assets),
    (11, "backfill_legacy_candidate_decisions", _backfill_legacy_candidate_decisions),
    (12, "track_task_stage_durations", _add_task_stage_durations),
    (13, "candidate_ownership_operations", _add_candidate_ownership_operations),
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply versioned AiWardrobe database migrations.")
    parser.add_argument("command", choices=("upgrade", "current"), default="upgrade", nargs="?")
    command = parser.parse_args().command
    engine = get_engine()
    if command == "upgrade":
        print(f"Database schema upgraded to version {upgrade_database(engine)}")
    else:
        print(f"Database schema version {current_version(engine)} / {LATEST_VERSION}")


if __name__ == "__main__":
    main()
