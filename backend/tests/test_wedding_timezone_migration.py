"""Synthetic, isolated local PostgreSQL migration regression; never a shared schema."""
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import json

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session

FIELDS = {'wedding_projects': ['last_activity_at', 'delivered_at'],
          'wedding_assets': ['quality_confirmed_at', 'invalidated_at'],
          'wedding_object_cleanup': ['writer_finished_at'],
          'wedding_render_versions': ['started_at', 'updated_at', 'invalidated_at']}


@pytest.fixture
def isolated_pg():
    root = Path(__file__).resolve().parents[2]
    path = root / '.superpowers/sdd/gap-integration-settings.json'
    if not path.exists():
        pytest.skip('Dedicated local PostgreSQL settings unavailable')
    config = json.loads(path.read_text(encoding='utf-8'))
    if config['postgres_host'] != '127.0.0.1' or config['postgres_database'] != 'wedding_gap_tests':
        pytest.skip('Dedicated local test database required')
    url = URL.create('postgresql+psycopg', username=config['postgres_user'],
        password=(root/config['postgres_password_file']).read_text().strip(),
        host=config['postgres_host'], port=config['postgres_port'], database=config['postgres_database'])
    admin = create_engine(url, connect_args={'connect_timeout': 5})
    schema = 'wedding_task5_tz_' + uuid4().hex
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    engine = create_engine(url.update_query_dict({'options':f'-csearch_path={schema} -ctimezone=Asia/Shanghai'}),connect_args={'connect_timeout':5})
    try:
        from app.database import Base
        import app.models  # Register complete metadata before create_all on a standalone test run.
        Base.metadata.create_all(engine)
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()


@pytest.mark.parametrize('schema_kind', ['missing', 'naive', 'current', 'naive_override'])
def test_additive_timestamps_preserve_absolute_time_and_accounting_twice(isolated_pg, schema_kind, monkeypatch):
    from app.models import User, WeddingProject, RenderVersion, WeddingAccountUsage
    from app.wedding_migrations import migrate_creation
    from app.wedding_service import _as_utc
    engine = isolated_pg
    instant = datetime(2026, 10, 1, 2, 30, tzinfo=timezone.utc)
    if schema_kind != 'current':
        with engine.begin() as conn:
            for table, fields in FIELDS.items():
                for field in fields:
                    conn.execute(text(f'ALTER TABLE {table} DROP COLUMN {field}'))
                    if schema_kind.startswith('naive'):
                        conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {field} TIMESTAMP'))
    # Migrate empty legacy shape first; then exercise writes in the non-UTC session.
    if schema_kind == 'missing':
        migrate_creation(engine)
    with Session(engine) as db:
        user = User(email='synthetic-only@example.com'); db.add(user); db.flush()
        project = WeddingProject(owner_id=user.id, title='TZ regression',last_activity_at=instant,delivered_at=instant,budget_spent_fen=11,budget_reserved_fen=7)
        db.add(project);db.flush()
        render = RenderVersion(project_id=project.id, project_version=1, version=1,status='processing',started_at=instant,updated_at=instant,timeline_hash='0'*64)
        db.add(render);db.flush()
        db.add(WeddingAccountUsage(owner_id=user.id,spent_fen=99,reserved_fen=88))
        db.commit(); project_id=project.id;render_id=render.id;owner_id=user.id
    if schema_kind == 'naive_override':
        monkeypatch.setenv('WEDDING_LEGACY_TIMESTAMP_TIMEZONE', 'Asia/Shanghai')
        from app.config import get_settings
        get_settings.cache_clear()
        with engine.begin() as conn:
            conn.execute(text("SET TIME ZONE 'UTC'"))
    migrate_creation(engine);migrate_creation(engine)
    with Session(engine) as db:
        project=db.get(WeddingProject,project_id);render=db.get(RenderVersion,render_id)
        for value in (project.last_activity_at,project.delivered_at,render.started_at,render.updated_at):
            assert _as_utc(value) == instant
        account=db.get(WeddingAccountUsage,owner_id)
        assert (account.spent_fen,account.reserved_fen)==(99,88)
        for table, fields in FIELDS.items():
            columns={column['name']:column for column in inspect(db.get_bind()).get_columns(table)}
            assert all(columns[field]['type'].timezone for field in fields)
