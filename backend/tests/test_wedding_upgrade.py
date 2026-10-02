import subprocess
import sys
import os
import json
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
import pytest
from sqlalchemy import create_engine, inspect, text


def test_explicit_upgrade_command_and_verify_only_startup(tmp_path, monkeypatch):
    url = 'sqlite:///' + str(tmp_path/'upgrade.db')
    monkeypatch.setenv('DATABASE_URL', url)
    monkeypatch.setenv('TESTING','true')
    monkeypatch.setenv('ENVIRONMENT','production')
    result = subprocess.run([sys.executable,'-m','app.migrations','upgrade'],capture_output=True,text=True)
    assert result.returncode == 0, result.stderr
    engine = create_engine(url)
    assert 'wedding_schema_versions' in inspect(engine).get_table_names()
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE wedding_schema_versions'))
    from app.config import get_settings
    from app.database import init_db, reset_database_connection
    get_settings.cache_clear(); reset_database_connection()
    with pytest.raises(RuntimeError, match='SCHEMA_UPGRADE_REQUIRED'):
        init_db()
    assert 'wedding_schema_versions' not in inspect(engine).get_table_names()


@pytest.mark.skipif(not os.environ.get('WEDDING_PG_SETTINGS'),reason='Isolated local PostgreSQL opt-in')
def test_actual_postgres_old_schema_concurrent_upgrades_and_cold_verify(monkeypatch):
    from sqlalchemy.engine import URL
    from app.database import Base
    from app import models  # noqa: F401
    settings=json.loads(Path(os.environ['WEDDING_PG_SETTINGS']).read_text(encoding='utf-8'))
    secret_path=Path(settings['postgres_password_file'])
    if not secret_path.is_absolute(): secret_path=Path(__file__).resolve().parents[2]/secret_path
    secret=secret_path.read_text(encoding='utf-8').strip()
    url=URL.create('postgresql+psycopg',username=settings['postgres_user'],password=secret,
        host=settings['postgres_host'],port=settings['postgres_port'],database=settings['postgres_database'])
    admin=create_engine(url)
    schema='finalfix_'+uuid4().hex
    assert schema.startswith('finalfix_') and len(schema)==41
    with admin.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA {schema}'))
    scoped=url.update_query_dict({'options':f'-csearch_path={schema}'})
    engine=create_engine(scoped)
    try:
        Base.metadata.create_all(engine)
        from sqlalchemy.orm import Session
        from datetime import datetime, timezone
        with Session(engine) as db:
            owner=models.User(email='synthetic-upgrade@example.test');db.add(owner);db.flush()
            project=models.WeddingProject(owner_id=owner.id,title='synthetic-upgrade');db.add(project);db.flush()
            asset=models.WeddingAsset(project_id=project.id,object_key='synthetic-upgrade',sha256='0'*64,mime='image/png',size_bytes=1)
            db.add(asset);db.commit()
        with engine.begin() as conn:
            conn.execute(text('ALTER TABLE wedding_assets DROP COLUMN provenance'))
            conn.execute(text('ALTER TABLE wedding_storyboard_shots DROP COLUMN source_shot_id'))
            conn.execute(text('ALTER TABLE wedding_storyboard_shots DROP COLUMN generation_attempts'))
            conn.execute(text('ALTER TABLE wedding_assets ALTER COLUMN invalidated_at TYPE TIMESTAMP WITHOUT TIME ZONE'))
            conn.execute(text("UPDATE wedding_assets SET invalidated_at=TIMESTAMP '2026-01-02 12:34:56'"))
        env={**os.environ,'DATABASE_URL':scoped.render_as_string(hide_password=False),'ENVIRONMENT':'production','TESTING':'false',
            'JWT_SECRET':'local-synthetic-upgrade-test-secret-32','WEDDING_LEGACY_TIMESTAMP_TIMEZONE':'Asia/Shanghai'}
        def run(command):
            return subprocess.run([sys.executable,*command],env=env,capture_output=True,timeout=120).returncode
        with ThreadPoolExecutor(max_workers=4) as pool:
            exits=list(pool.map(lambda _:run(['-m','app.migrations','upgrade']),range(4)))
        assert exits==[0]*4, 'Concurrent upgrade process failed (private output withheld)'
        with engine.connect() as conn:
            assert conn.scalar(text('SELECT MAX(version) FROM wedding_schema_versions'))==2
            assert conn.scalar(text('SELECT invalidated_at FROM wedding_assets')) == datetime(2026,1,2,4,34,56,tzinfo=timezone.utc)
            assert next(c for c in inspect(conn).get_columns('wedding_assets') if c['name']=='invalidated_at')['type'].timezone
        with ThreadPoolExecutor(max_workers=4) as pool:
            exits=list(pool.map(lambda _:run(['-c','from app.database import init_db; init_db()']),range(4)))
        assert exits==[0]*4, 'Cold verify-only process failed (private output withheld)'
    finally:
        engine.dispose()
        with admin.begin() as conn:
            conn.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        admin.dispose()
