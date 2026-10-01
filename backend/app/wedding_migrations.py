"""Additive SQLite/PostgreSQL creation migration and once-only account backfill."""
from sqlalchemy import inspect, text, literal
from sqlalchemy.exc import DataError


def migrate_creation(engine):
    with engine.begin() as conn:
        postgres = conn.dialect.name == 'postgresql'
        zone_literal = None
        columns = {c['name'] for c in inspect(conn).get_columns('wedding_participants')}
        if 'primary_photo_asset_id' not in columns:
            conn.execute(text('ALTER TABLE wedding_participants ADD COLUMN primary_photo_asset_id VARCHAR(36) REFERENCES wedding_assets(id) ON DELETE SET NULL'))
        additions = {
            'wedding_projects': {'last_activity_at':'TIMESTAMP', 'delivered_at':'TIMESTAMP', 'final_download_count':'INTEGER NOT NULL DEFAULT 0', 'preview_bytes_served':'BIGINT NOT NULL DEFAULT 0', 'selected_music': "JSON NOT NULL DEFAULT '{}'"},
            'wedding_assets': {'quality': "JSON NOT NULL DEFAULT '{}'", 'quality_confirmed_at': 'TIMESTAMP', 'invalidated_at': 'TIMESTAMP'},
            'wedding_object_cleanup': {'writer_finished_at':'TIMESTAMP'},
            'wedding_render_versions': {'lease_token':'VARCHAR(36)', 'started_at':'TIMESTAMP', 'updated_at':'TIMESTAMP', 'attempts':'INTEGER NOT NULL DEFAULT 0', 'error_code':'VARCHAR(80)', 'timeline_snapshot': "JSON NOT NULL DEFAULT '[]'", 'music_snapshot': "JSON NOT NULL DEFAULT '{}'", 'restored_from_id': 'VARCHAR(36)', 'invalidated_at': 'TIMESTAMP'},
        }
        for table, fields in additions.items():
            existing = {c['name']: c for c in inspect(conn).get_columns(table)}
            for field, sql_type in fields.items():
                if field not in existing:
                    if postgres and sql_type == 'TIMESTAMP':
                        sql_type = 'TIMESTAMP WITH TIME ZONE'
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {field} {sql_type}'))
                elif postgres and sql_type == 'TIMESTAMP' and not existing[field]['type'].timezone:
                    if zone_literal is None:
                        from app.config import get_settings
                        # Resolve historical wall-time provenance only for naive
                        # conversion. PostgreSQL also accepts fixed-offset/POSIX
                        # zones absent from pg_timezone_names; validate by binding.
                        legacy_zone = get_settings().wedding_legacy_timestamp_timezone or conn.scalar(text("SELECT current_setting('TimeZone')"))
                        try:
                            conn.scalar(text("SELECT TIMESTAMP '2000-01-01 00:00:00' AT TIME ZONE :zone"), {'zone': legacy_zone})
                        except DataError as error:
                            if getattr(error.orig, 'sqlstate', None) != '22023':
                                raise
                            raise ValueError('WEDDING_LEGACY_TIMESTAMP_TIMEZONE_INVALID') from error
                        # Utility DDL needs an escaped literal, never raw input.
                        zone_literal = str(literal(legacy_zone).compile(dialect=conn.dialect, compile_kwargs={'literal_binds':True}))
                    conn.execute(text(f'ALTER TABLE {table} ALTER COLUMN {field} TYPE TIMESTAMP WITH TIME ZONE '
                        f'USING {field} AT TIME ZONE {zone_literal}'))
        conn.execute(text('UPDATE wedding_projects SET last_activity_at = updated_at WHERE last_activity_at IS NULL'))
        # Legacy NULL asset deadlines get one bounded retention window; existing deadlines stay intact.
        from datetime import datetime, timedelta, timezone
        conn.execute(text('UPDATE wedding_assets SET expires_at = :deadline WHERE expires_at IS NULL'), {'deadline':(datetime.now(timezone.utc)+timedelta(days=30)).isoformat()})
        # Existing counters already include active reservations. Never recompute an
        # existing account from live projects: deleted usage must remain charged.
        conn.execute(text('INSERT INTO wedding_account_usage (owner_id, spent_fen, reserved_fen) '
            'SELECT owner_id, SUM(budget_spent_fen), SUM(budget_reserved_fen) FROM wedding_projects '
            'WHERE owner_id NOT IN (SELECT owner_id FROM wedding_account_usage) GROUP BY owner_id'))
        conn.execute(text("INSERT INTO wedding_billing_receipts (id, operation_key, owner_id, project_id, job_id, provider, model, state, reserved_fen, estimated_fen, settled, provenance, usage, price_metadata, created_at) "
            "SELECT l.id, 'legacy:' || l.id, p.owner_id, p.id, l.generation_job_id, 'legacy_unknown', 'legacy_unknown', l.status, l.reserved_fen, l.actual_fen, CASE WHEN l.status = 'settled' THEN TRUE ELSE FALSE END, '{}', '{}', '{}', l.created_at "
            "FROM wedding_budget_ledger l JOIN wedding_projects p ON p.id=l.project_id "
            "WHERE NOT EXISTS (SELECT 1 FROM wedding_billing_receipts r WHERE r.job_id=l.generation_job_id)"))
