"""Public regression boundaries from the independent integrated-main review."""
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.database import get_session_factory
from app.models import GenerationJob, StoryboardShot, WeddingBillingReceipt, WeddingProject, utc_now
from app.wedding_media import ProviderUnavailable, build_provider_bundle
from app.wedding_worker import process_next_generation_job
from tests.test_wedding_final_guards import ready, enqueue_video


def test_crash_unknown_video_blocks_new_key_until_authoritative_resolution(client, monkeypatch):
    from app.wedding_worker import claim_next_generation_job, _dispatch_preflight, _recover_stale_generation_jobs
    from app.wedding_accounting import reconcile_rows
    base, actors, _, board, _ = ready(client, monkeypatch)
    shot = next(s for s in board['shots'] if s['shot_type'] == 'scene_ai')
    first = enqueue_video(client, base, actors['A'], 'crash-first-video', shot).json()
    claim = claim_next_generation_job()
    request, error = _dispatch_preflight(get_session_factory(), first['id'], shot['id'], claim.lease_token, mark_dispatching=True)
    assert request and error is None
    with get_session_factory()() as db:
        db.get(GenerationJob, first['id']).updated_at = utc_now() - timedelta(days=1)
        db.commit()
    _recover_stale_generation_jobs(get_session_factory())
    retry = enqueue_video(client, base, actors['A'], 'crash-fresh-video', shot)
    assert retry.status_code == 409, retry.text
    assert retry.json()['detail'] == 'PROVIDER_RECONCILIATION_REQUIRED'
    facts = board['facts']
    revision = {'version': 1, 'story_text': board['story_text'],
        'facts': [{k: f[k] for k in ('source_field', 'text')} for f in facts],
        'shots': [{**{k: s[k] for k in ('shot_index', 'shot_type', 'duration_ms', 'person_keys', 'description', 'subtitle', 'creative_scene')},
            'fact_indexes': [i for i, f in enumerate(facts) if f['id'] in s['fact_ids']]} for s in board['shots']]}
    revised = client.post(base + '/storyboard/revisions', headers=actors['A'], json=revision)
    assert revised.status_code == 409 and revised.json()['detail'] == 'PROVIDER_RECONCILIATION_REQUIRED'
    with get_session_factory()() as db:
        assert db.get(GenerationJob, first['id']).status == 'needs_action'
        assert db.get(WeddingProject, first['project_id']).budget_reserved_fen > 0
        assert db.get(StoryboardShot, shot['id']).generation_attempts == 1
    row = {'operation_key': f"{first['id']}:{shot['id']}", 'terminal_state': 'not_submitted',
           'cost_fen': 0, 'evidence_id': 'synthetic-authoritative-provider-audit'}
    assert reconcile_rows(get_session_factory(), [row, row]) == [{'status': 'applied'}, {'status': 'duplicate'}]
    assert enqueue_video(client, base, actors['A'], 'verified-fresh-video', shot).status_code == 202
    assert process_next_generation_job().status == 'completed'
    with get_session_factory()() as db:
        assert db.get(StoryboardShot, shot['id']).generation_attempts == 1


@pytest.mark.parametrize('scope', ['one', 'all'])
def test_queued_cancellation_returns_slots_once_and_allows_fourth_submission(client, monkeypatch, scope):
    base, actors, _, board, _ = ready(client, monkeypatch)
    shot = next(s for s in board['shots'] if s['shot_type'] == 'scene_ai') if scope == 'one' else None
    for i in range(4):
        job = enqueue_video(client, base, actors['A'], f'undispatched-{scope}-{i}', shot)
        assert job.status_code == 202, job.text
        for _ in range(2):
            assert client.post(base + f"/generation-jobs/{job.json()['id']}/cancel", headers=actors['A']).status_code == 200
        with get_session_factory()() as db:
            assert all(s.generation_attempts == 0 for s in db.scalars(select(StoryboardShot).where(StoryboardShot.project_id == base.rsplit('/', 1)[1])))


@pytest.mark.parametrize('state', ['not_submitted', 'failed', 'unknown'])
def test_only_certain_non_submission_returns_attempt(client, monkeypatch, state):
    base, actors, _, board, _ = ready(client, monkeypatch)
    shot = next(s for s in board['shots'] if s['shot_type'] == 'scene_ai')
    response = enqueue_video(client, base, actors['A'], 'preflight-attempt', shot)
    assert response.status_code == 202
    class Provider:
        def generate(self, request):
            raise ProviderUnavailable('SYNTHETIC', 'Synthetic', submission_state=state)
    process_next_generation_job(providers=replace(build_provider_bundle(), scene_video=Provider()))
    with get_session_factory()() as db:
        assert db.get(StoryboardShot, shot['id']).generation_attempts == (0 if state == 'not_submitted' else 1)
        receipt = db.scalar(select(WeddingBillingReceipt).where(WeddingBillingReceipt.operation_key == response.json()['id'] + ':' + shot['id']))
        assert receipt.state == state


def test_known_cancelled_terminal_recovery_allows_continuation(client, monkeypatch):
    from tests.test_wedding_final_guards import test_cancelled_known_task_resumes_only_get_after_restart
    test_cancelled_known_task_resumes_only_get_after_restart(client, monkeypatch)
    with get_session_factory()() as db:
        project = db.scalar(select(WeddingProject))
        assert project.status == 'ready_to_generate'
        project_id = project.id
        owner_id = project.owner_id
    from app.security import create_access_token
    headers = {'Authorization': 'Bearer ' + create_access_token(owner_id)}
    assert enqueue_video(client, '/wedding-projects/' + project_id, headers, 'resume-after-cancel').status_code == 202


@pytest.mark.parametrize('secret', [None, '', 'short', 'change-me',
    'docker-development-secret-with-at-least-thirty-two-bytes', 'replace-with-a-random-32-byte-minimum-secret'])
def test_production_rejects_missing_and_public_signing_keys(secret):
    from app.config import Settings
    from pydantic import ValidationError
    class IsolatedSettings(Settings):
        @classmethod
        def settings_customise_sources(cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings):
            return (init_settings,)
    values = dict(environment='production', testing=False, database_url='postgresql+psycopg://synthetic@localhost/test')
    if secret is not None:
        values['jwt_secret'] = secret
    with pytest.raises(ValidationError) as error:
        IsolatedSettings(**values)
    assert 'JWT_SECRET' in str(error.value)
    if secret and secret != 'short':
        assert secret not in str(error.value)


def test_compose_production_secret_is_required_and_shared():
    from pathlib import Path
    import re
    content = (Path(__file__).resolve().parents[2] / 'docker-compose.yml').read_text()
    expression = re.search(r'^  JWT_SECRET: (.+)$', content, re.M).group(1)
    assert expression.startswith('${JWT_SECRET:?') and expression.endswith('}')
    for service in ('migrate', 'backend', 'worker', 'wedding-worker'):
        block = re.split(r'\n  [a-z][a-z-]*:\n', content.split('\n  ' + service + ':\n', 1)[1])[0]
        assert 'environment: *backend-environment' in block


def test_unique_production_key_authenticates_both_domains_and_rejects_public_key(client, monkeypatch):
    import secrets
    import jwt
    from fastapi.security import HTTPAuthorizationCredentials
    from fastapi import HTTPException
    from app.config import Settings, get_settings
    from app.models import User
    from app.security import create_access_token, get_current_user
    from app.wedding_service import get_wedding_actor, require_project_owner
    unique = secrets.token_urlsafe(48)
    settings = Settings(_env_file=None, environment='production', testing=False,
                        database_url='postgresql+psycopg://synthetic@localhost/test', jwt_secret=unique)
    assert settings.jwt_secret == unique
    monkeypatch.setenv('JWT_SECRET', unique)
    get_settings.cache_clear()
    with get_session_factory()() as db:
        user = User(email='synthetic-auth@example.test'); db.add(user); db.flush()
        project = WeddingProject(owner_id=user.id, title='Synthetic auth'); db.add(project); db.flush()
        valid = HTTPAuthorizationCredentials(scheme='Bearer', credentials=create_access_token(user.id))
        assert get_current_user(valid, db).id == user.id
        assert require_project_owner(db, get_wedding_actor(valid, db), project.id).id == project.id
        forged = HTTPAuthorizationCredentials(scheme='Bearer', credentials=jwt.encode({'sub': user.id},
            'docker-development-secret-with-at-least-thirty-two-bytes', algorithm='HS256'))
        for authenticate in (get_current_user, get_wedding_actor):
            with pytest.raises(HTTPException) as rejected:
                authenticate(forged, db)
            assert rejected.value.status_code == 401


def test_acceptance_legacy_fixture_upgrades_both_ledgers_twice(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from scripts.check_wedding_pg_s3 import prepare_legacy_schema
    from app.migrations import upgrade_database, verify
    engine = create_engine('sqlite:///' + str(tmp_path / 'legacy.db'))
    prepare_legacy_schema(engine)
    assert 'wedding_schema_versions' not in inspect(engine).get_table_names()
    assert 'schema_migrations' not in inspect(engine).get_table_names()
    assert 'attempts' not in {c['name'] for c in inspect(engine).get_columns('wedding_render_versions')}
    for _ in range(2):
        assert upgrade_database(engine) == 13
        verify(engine)
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT MAX(version) FROM schema_migrations')) == 13
        assert conn.scalar(text('SELECT MAX(version) FROM wedding_schema_versions')) == 2
    engine.dispose()


def test_predispatch_storage_failure_returns_reserved_attempt(client, monkeypatch):
    from app.wedding_service import WeddingPrivateStorage
    base, actors, _, board, _ = ready(client, monkeypatch)
    shot = next(s for s in board['shots'] if s['shot_type'] == 'scene_ai')
    job = enqueue_video(client, base, actors['A'], 'storage-predispatch', shot).json()
    def missing(*args):
        raise OSError('synthetic missing bytes')
    monkeypatch.setattr(WeddingPrivateStorage, 'read', missing)
    assert process_next_generation_job().status == 'failed'
    with get_session_factory()() as db:
        assert db.get(StoryboardShot, shot['id']).generation_attempts == 0
        assert db.scalar(select(WeddingBillingReceipt.id).where(WeddingBillingReceipt.job_id == job['id'], WeddingBillingReceipt.provider != 'budget_envelope')) is None
    assert enqueue_video(client, base, actors['A'], 'retry-storage-predispatch', shot).status_code == 202


@pytest.mark.parametrize('fence', ['blocked', 'cancelled', 'revoked', 'other_active'])
def test_cancelled_terminal_reconciliation_preserves_project_fences(client, monkeypatch, fence):
    from app.models import WeddingParticipant
    from app.wedding_reconciliation import reconcile_cancelled
    base, actors, _, board, _ = ready(client, monkeypatch)
    shot = next(s for s in board['shots'] if s['shot_type'] == 'scene_ai')
    job = enqueue_video(client, base, actors['A'], 'fenced-cancel-reconcile', shot).json()
    class Running:
        def generate(self, request):
            if not request.existing_task_id:
                request.checkpoint('synthetic-running')
                raise ProviderUnavailable('PENDING', 'Pending', task_id='synthetic-running', submission_state='running')
            assert request.reconcile_only
            return replace(build_provider_bundle().scene_video.generate(request), task_id='synthetic-running')
    providers = replace(build_provider_bundle(), scene_video=Running())
    assert process_next_generation_job(providers=providers).status == 'processing'
    assert client.post(base + f"/generation-jobs/{job['id']}/cancel", headers=actors['A']).status_code == 200
    with get_session_factory()() as db:
        current = db.get(GenerationJob, job['id']); current.updated_at = utc_now() - timedelta(days=1)
        project = db.get(WeddingProject, job['project_id'])
        if fence in ('blocked', 'cancelled'):
            project.status = fence
        elif fence == 'revoked':
            person = db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id == project.id))
            person.consent_status = 'revoked'; person.revoked_at = utc_now()
        else:
            db.add(GenerationJob(project_id=project.id, story_version_id=current.story_version_id,
                quote_id=current.quote_id, project_version=1, idempotency_key='synthetic-other-active',
                request_fingerprint='synthetic', status='queued', total_shots=1))
        db.commit()
    assert reconcile_cancelled(get_session_factory(), providers).status == 'cancelled'
    with get_session_factory()() as db:
        assert db.get(WeddingProject, job['project_id']).status == (fence if fence in ('blocked', 'cancelled') else 'generating')


def test_development_and_explicit_testing_keep_existing_key_behavior():
    from app.config import Settings
    for environment, testing in [('development', False), ('production', True)]:
        assert Settings(_env_file=None, environment=environment, testing=testing, jwt_secret='change-me').jwt_secret == 'change-me'


def test_authoritative_video_resolution_is_idempotent_while_sibling_is_unknown(client, monkeypatch):
    from app.wedding_accounting import reconcile_rows
    from app.wedding_video_operations import prepare_receipt
    base, actors, _, board, _ = ready(client, monkeypatch)
    job = enqueue_video(client, base, actors['A'], 'two-unknown-operations').json()
    ids = [s['id'] for s in board['shots'] if s['shot_type'] == 'scene_ai'][:2]
    with get_session_factory()() as db:
        project = db.get(WeddingProject, job['project_id']); current = db.get(GenerationJob, job['id'])
        for sid in ids:
            prepare_receipt(db, project, current, db.get(StoryboardShot, sid))
        current.status = 'needs_action'; db.commit()
    rows = [{'operation_key': job['id'] + ':' + sid, 'terminal_state': 'not_submitted', 'cost_fen': 0,
             'evidence_id': 'synthetic-provider-evidence'} for sid in ids]
    assert reconcile_rows(get_session_factory(), [rows[0], rows[0]]) == [{'status': 'applied'}, {'status': 'duplicate'}]
    with get_session_factory()() as db:
        assert db.get(WeddingProject, job['project_id']).budget_reserved_fen > 0
    assert reconcile_rows(get_session_factory(), [rows[1]]) == [{'status': 'applied'}]
    with get_session_factory()() as db:
        assert db.get(WeddingProject, job['project_id']).budget_reserved_fen == 0
        assert all(s.generation_attempts == 0 for s in db.scalars(select(StoryboardShot).where(StoryboardShot.project_id == job['project_id'])))
