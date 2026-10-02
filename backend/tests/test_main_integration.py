from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, inspect, text


def test_both_schema_ledgers_upgrade_on_one_connection_and_rollback(tmp_path, monkeypatch):
    from app import migrations
    engine = create_engine(f"sqlite:///{tmp_path / 'atomic.db'}", pool_size=1, max_overflow=0, pool_timeout=1)
    original = migrations._upgrade_wedding
    def fail(conn):
        original(conn)
        raise RuntimeError('interrupted deployment')
    monkeypatch.setattr(migrations, '_upgrade_wedding', fail)
    with pytest.raises(RuntimeError, match='interrupted deployment'):
        migrations.upgrade_database(engine)
    assert inspect(engine).get_table_names() == []
    monkeypatch.setattr(migrations, '_upgrade_wedding', original)
    assert migrations.upgrade_database(engine) == 13
    migrations.verify(engine)
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT COUNT(*) FROM schema_migrations')) == 13
        assert conn.scalar(text('SELECT MAX(version) FROM wedding_schema_versions')) == 2
    engine.dispose()


@pytest.mark.parametrize('wardrobe_current', [False, True])
def test_combined_upgrade_old_wedding_foreign_key_and_concurrent_cold_start(tmp_path, wardrobe_current):
    from app import migrations, models
    from app.database import Base
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}", connect_args={'timeout': 20})
    migrations.upgrade_database(engine)
    with engine.begin() as conn:
        conn.execute(text('DROP TABLE wedding_schema_versions'))
        # Recreate an empty legacy table without the later FK column/constraint.
        from sqlalchemy.schema import CreateTable
        ddl = str(CreateTable(models.WeddingParticipant.__table__).compile(engine))
        ddl = '\n'.join(line for line in ddl.splitlines() if 'primary_photo_asset_id' not in line)
        conn.execute(text('DROP TABLE wedding_participants'))
        conn.execute(text(ddl))
        conn.execute(text('ALTER TABLE wedding_assets DROP COLUMN provenance'))
        if not wardrobe_current:
            conn.execute(text('DROP TABLE schema_migrations'))
    with ThreadPoolExecutor(max_workers=3) as pool:
        assert list(pool.map(lambda _: migrations.upgrade_database(engine), range(3))) == [13] * 3
    migrations.verify(engine)
    assert 'primary_photo_asset_id' in {c['name'] for c in inspect(engine).get_columns('wedding_participants')}
    engine.dispose()


@pytest.mark.parametrize('missing', [None, 'schema_migrations', 'wedding_schema_versions'])
def test_production_startup_checks_both_ledgers_without_writes(tmp_path, monkeypatch, missing):
    from app.config import get_settings
    from app.database import get_engine, init_db, reset_database_connection
    from app.migrations import upgrade_database
    monkeypatch.setenv('DATABASE_URL', f"sqlite:///{tmp_path / 'startup.db'}")
    monkeypatch.setenv('ENVIRONMENT', 'production')
    monkeypatch.setenv('TESTING', 'true')
    get_settings.cache_clear(); reset_database_connection()
    engine = get_engine()
    upgrade_database(engine)
    if missing:
        with engine.begin() as conn:
            conn.execute(text(f'DROP TABLE {missing}'))
    statements = []
    event.listen(engine, 'before_cursor_execute', lambda conn, cursor, statement, params, context, many: statements.append(statement))
    if missing:
        with pytest.raises(RuntimeError):
            init_db()
    else:
        init_db()
    assert not any(s.lstrip().upper().startswith(('CREATE', 'ALTER', 'INSERT', 'UPDATE', 'DELETE')) for s in statements)
    assert missing not in inspect(engine).get_table_names()
    reset_database_connection(); get_settings.cache_clear()


def test_local_wedding_worker_is_managed_with_same_environment_and_stop_manifest(tmp_path, monkeypatch):
    from app.local_launcher import service_commands, stop_local
    commands = service_commands(tmp_path, 'local')
    assert set(commands) == {'api', 'worker', 'wedding-worker', 'frontend'}
    assert commands['wedding-worker'][0][-4:] == ['-m', 'app.wedding_worker', '--poll-seconds', '2']
    assert commands['wedding-worker'][1] == commands['worker'][1]
    import json
    records = tmp_path / '.local-services'; records.mkdir()
    (records / 'pids.json').write_text(json.dumps({'wedding-worker': {'pid': 12, 'identity': 'synthetic'}}))
    stopped=[]
    monkeypatch.setattr('app.local_launcher._stop_record', lambda record: stopped.append(record))
    assert stop_local(tmp_path) == ['wedding-worker']
    assert stopped == [{'pid': 12, 'identity': 'synthetic'}]


def test_managed_wedding_process_consumes_only_its_queue(client, monkeypatch):
    import os
    import subprocess
    from conftest import jpeg_bytes
    from tests.test_wedding_creation import setup_project, submit
    from app.local_launcher import process_options, service_commands
    project_id, headers = setup_project(client, monkeypatch)
    story = submit(client, project_id, headers)
    assert story.status_code == 202
    upload = client.post('/v2/tasks/garment-upload',
        headers={**headers, 'Idempotency-Key': '22222222-3333-4444-8555-666666666666'},
        files={'file': ('synthetic.jpg', jpeg_bytes(), 'image/jpeg')})
    assert upload.status_code == 202
    root = Path(__file__).resolve().parents[2]
    command, directory = service_commands(root, 'wedding-worker')['wedding-worker']
    result = subprocess.run([*command, '--once'], cwd=directory, env=dict(os.environ),
        capture_output=True, timeout=60, **process_options())
    assert result.returncode == 0, result.stderr.decode(errors='replace')
    jobs = client.get(f'/wedding-projects/{project_id}/creation-jobs', headers=headers).json()
    assert jobs[0]['status'] == 'completed'
    storyboard = client.get(f'/wedding-projects/{project_id}/storyboard', headers=headers).json()
    assert len(storyboard['shots']) == 18
    assert storyboard['status'] == 'draft'
    assert client.get(f"/v2/tasks/{upload.json()['id']}", headers=headers).json()['status'] == 'queued'
