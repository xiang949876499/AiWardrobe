"""Explicit serialized schema upgrade; API/worker production startup is read-only."""
from contextlib import contextmanager
import argparse
from sqlalchemy import inspect, text
from app.database import Base, get_engine

VERSION = 2


@contextmanager
def migration_transaction(engine):
    with engine.connect() as conn:
        if conn.dialect.name == 'postgresql':
            # Session lock covers create_all and every upgrade on the SAME connection.
            # Do not inherit the runtime's short lock timeout for deployment upgrades.
            conn.execute(text("SET lock_timeout = '60s'"))
            conn.execute(text("SET statement_timeout = '120s'"))
            conn.execute(text('SELECT pg_advisory_lock(741903280)'))
            conn.commit()
        try:
            if conn.dialect.name == 'sqlite':
                conn.exec_driver_sql('BEGIN IMMEDIATE')
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            if conn.dialect.name == 'postgresql':
                conn.execute(text('SELECT pg_advisory_unlock(741903280)'))
                conn.commit()


def upgrade(engine=None):
    from app import models  # noqa: F401
    from app.database import _ensure_lightweight_columns, _ensure_wedding_project_columns
    from app.wedding_migrations import migrate_creation
    with migration_transaction(engine or get_engine()) as conn:
        conn.execute(text('CREATE TABLE IF NOT EXISTS wedding_schema_versions (version INTEGER PRIMARY KEY)'))
        current = conn.scalar(text('SELECT COALESCE(MAX(version),0) FROM wedding_schema_versions'))
        if current > VERSION:
            raise RuntimeError('SCHEMA_VERSION_NEWER_THAN_APPLICATION')
        if current < 1:
            Base.metadata.create_all(bind=conn)
            _ensure_lightweight_columns(conn)
            _ensure_wedding_project_columns(conn)
            migrate_creation(conn)
            conn.execute(text('INSERT INTO wedding_schema_versions (version) VALUES (1)'))
        if current < 2:
            additions = {
                'wedding_assets': {'provenance': "JSON NOT NULL DEFAULT '{}'"},
                'wedding_storyboard_shots': {'source_shot_id':'VARCHAR(36)', 'generation_attempts':'INTEGER NOT NULL DEFAULT 0'},
                'wedding_shot_candidates': {'frame_asset_id':'VARCHAR(36)'},
                'wedding_consent_invites': {'kind':"VARCHAR(16) NOT NULL DEFAULT 'consent'", 'disclosure':"JSON NOT NULL DEFAULT '{}'"},
            }
            for table, fields in additions.items():
                existing = {c['name'] for c in inspect(conn).get_columns(table)}
                for field, ddl in fields.items():
                    if field not in existing:
                        conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {field} {ddl}'))
            # Historical attempts survive candidate deletion and future input reuse.
            conn.execute(text('UPDATE wedding_storyboard_shots SET generation_attempts = '
                '(SELECT COUNT(*) FROM wedding_billing_receipts r WHERE r.operation_key LIKE '\
                "'%:' || wedding_storyboard_shots.id AND r.state NOT IN ('reserved','not_submitted'))"))
            conn.execute(text('INSERT INTO wedding_schema_versions (version) VALUES (2)'))


def verify(engine=None):
    from app import models  # noqa: F401
    with (engine or get_engine()).connect() as conn:
        if 'wedding_schema_versions' not in inspect(conn).get_table_names():
            raise RuntimeError('SCHEMA_UPGRADE_REQUIRED: run python -m app.migrations upgrade')
        if conn.scalar(text('SELECT MAX(version) FROM wedding_schema_versions')) != VERSION:
            raise RuntimeError('SCHEMA_UPGRADE_REQUIRED: run python -m app.migrations upgrade')

        inspector = inspect(conn)
        tables = set(inspector.get_table_names())
        for table in Base.metadata.sorted_tables:
            if table.name not in tables or not set(table.columns.keys()) <= {c['name'] for c in inspector.get_columns(table.name)}:
                raise RuntimeError('SCHEMA_UPGRADE_REQUIRED: schema does not match recorded version')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['upgrade','verify'])
    args = parser.parse_args()
    (upgrade if args.command == 'upgrade' else verify)()
    print('Schema ' + args.command + ' complete')


if __name__ == '__main__':
    main()
