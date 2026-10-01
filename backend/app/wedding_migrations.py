"""Additive SQLite/PostgreSQL creation migration and once-only account backfill."""
from sqlalchemy import inspect, text


def migrate_creation(engine):
    with engine.begin() as conn:
        columns = {c['name'] for c in inspect(conn).get_columns('wedding_participants')}
        if 'primary_photo_asset_id' not in columns:
            conn.execute(text('ALTER TABLE wedding_participants ADD COLUMN primary_photo_asset_id VARCHAR(36) REFERENCES wedding_assets(id) ON DELETE SET NULL'))
        additions = {
            'wedding_projects': {'selected_music': "JSON NOT NULL DEFAULT '{}'"},
            'wedding_assets': {'quality': "JSON NOT NULL DEFAULT '{}'", 'quality_confirmed_at': 'TIMESTAMP', 'invalidated_at': 'TIMESTAMP'},
            'wedding_render_versions': {'timeline_snapshot': "JSON NOT NULL DEFAULT '[]'", 'music_snapshot': "JSON NOT NULL DEFAULT '{}'", 'restored_from_id': 'VARCHAR(36)', 'invalidated_at': 'TIMESTAMP'},
        }
        for table, fields in additions.items():
            existing = {c['name'] for c in inspect(conn).get_columns(table)}
            for field, sql_type in fields.items():
                if field not in existing:
                    conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {field} {sql_type}'))
        # Existing counters already include active reservations. Never recompute an
        # existing account from live projects: deleted usage must remain charged.
        conn.execute(text('INSERT INTO wedding_account_usage (owner_id, spent_fen, reserved_fen) '
            'SELECT owner_id, SUM(budget_spent_fen), SUM(budget_reserved_fen) FROM wedding_projects '
            'WHERE owner_id NOT IN (SELECT owner_id FROM wedding_account_usage) GROUP BY owner_id'))
        conn.execute(text("INSERT INTO wedding_billing_receipts (id, operation_key, owner_id, project_id, job_id, provider, model, state, reserved_fen, estimated_fen, settled, provenance, usage, price_metadata, created_at) "
            "SELECT l.id, 'legacy:' || l.id, p.owner_id, p.id, l.generation_job_id, 'legacy_unknown', 'legacy_unknown', l.status, l.reserved_fen, l.actual_fen, CASE WHEN l.status = 'settled' THEN TRUE ELSE FALSE END, '{}', '{}', '{}', l.created_at "
            "FROM wedding_budget_ledger l JOIN wedding_projects p ON p.id=l.project_id "
            "WHERE NOT EXISTS (SELECT 1 FROM wedding_billing_receipts r WHERE r.job_id=l.generation_job_id)"))
