from datetime import timedelta
from uuid import uuid4
import anyio
import pytest
from sqlalchemy import select
from tests.conftest import login, jpeg_bytes


@pytest.fixture(autouse=True)
def forbid_unmocked_provider_network(monkeypatch):
    import httpx
    async def forbidden(*args, **kwargs):
        raise AssertionError("Provider HTTP must be mocked in recovery tests")
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)


def create(client, mode='single'):
    headers = {'Authorization': f'Bearer {login(client)}', 'Idempotency-Key': str(uuid4())}
    response = client.post('/v2/tasks/garment-upload', headers=headers, data={'mode': mode}, files={'file': ('shirt.jpg', jpeg_bytes(), 'image/jpeg')})
    assert response.status_code == 202
    return response.json()['id'], headers


@pytest.mark.parametrize('state', ['queued', 'running', 'cancelled'])
def test_retry_rejects_non_failed_task(client, state):
    from app.database import get_session_factory
    from app.models import AnalysisTask
    task_id, headers = create(client)
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, task_id)
        task.status = state
        task.cancel_requested = state == 'cancelled'
        task.lease_token = 'active' if state == 'running' else None
        db.commit()
    headers['Idempotency-Key'] = str(uuid4())
    assert client.post(f'/v2/tasks/{task_id}/retry', headers=headers, json={}).status_code == 409


def test_retry_replay_survives_completion_and_clears_flags(client):
    from app.database import get_session_factory
    from app.models import AnalysisTask, utc_now
    task_id, headers = create(client)
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, task_id)
        task.status = 'failed'
        task.cancel_requested = True
        task.lease_token = 'stale'
        task.lease_expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    headers['Idempotency-Key'] = str(uuid4())
    first = client.post(f'/v2/tasks/{task_id}/retry', headers=headers, json={})
    assert first.status_code == 202
    assert not first.json()['cancel_requested']
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, task_id)
        assert task.lease_token is None
        task.status = 'succeeded'
        db.commit()
    replay = client.post(f'/v2/tasks/{task_id}/retry', headers=headers, json={})
    assert replay.status_code == 202
    assert replay.json() == first.json()


@pytest.mark.parametrize('provider', ['runninghub', 'comfyui'])
def test_accepted_split_recovers_without_resubmission(client, monkeypatch, provider):
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, ProviderOperation, utc_now
    from app.task_worker import TaskWorker
    from app.runninghub import RunningHubClient, RunningHubOutput
    from app.comfyui import ComfyUIClient, ComfyUIOutput
    task_id, headers = create(client, 'split')
    calls = {'submit': 0, 'query': 0}
    async def submit(self, *args, **kwargs):
        calls['submit'] += 1
        return 'accepted-remote-id'
    async def collect(self, remote_id):
        calls['query'] += 1
        with get_session_factory()() as db:
            op = db.scalar(select(ProviderOperation).where(ProviderOperation.task_id == task_id))
            assert op.provider_task_id == remote_id
        if calls['query'] == 1:
            raise TimeoutError('accepted then connection lost')
        if provider == 'runninghub':
            return [RunningHubOutput('top.jpg', 'image/jpeg', jpeg_bytes(), 'https://test/top.jpg')]
        return [ComfyUIOutput('top', 'top.jpg', '', 'output', jpeg_bytes())]
    cls = RunningHubClient if provider == 'runninghub' else ComfyUIClient
    monkeypatch.setattr(cls, 'submit_garment_recognition', submit, raising=False)
    monkeypatch.setattr(cls, 'collect_garment_recognition', collect, raising=False)
    settings = get_settings().model_copy(update={'workflow_provider': provider})
    worker = TaskWorker(get_session_factory(), settings)
    assert anyio.run(worker.run_once)
    with get_session_factory()() as db:
        op = db.scalar(select(ProviderOperation).where(ProviderOperation.task_id == task_id))
        assert op.provider_task_id == 'accepted-remote-id'
        assert op.slot_released_at is None
        task = db.get(AnalysisTask, task_id)
        task.next_attempt_at = utc_now() - timedelta(seconds=1)
        op.reconciliation_evidence = {**op.reconciliation_evidence, 'last_query_at': (utc_now() - timedelta(seconds=31)).isoformat()}
        db.commit()
    assert anyio.run(TaskWorker(get_session_factory(), settings).run_once)
    assert calls == {'submit': 1, 'query': 2}
    with get_session_factory()() as db:
        assert db.get(AnalysisTask, task_id).status == 'succeeded'

@pytest.mark.parametrize('cancelled', [False, True])
def test_restart_known_id_cutoff_then_explicit_query(client, monkeypatch, cancelled):
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, ProviderOperation, utc_now
    from app.task_worker import TaskWorker
    from app.runninghub import RunningHubClient, RunningHubOutput
    from app.provider_capacity import reserve_provider_slot
    task_id, headers = create(client, 'split')
    now = utc_now()
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, task_id)
        task.status = 'running'
        task.lease_token = 'dead-worker'
        task.lease_expires_at = now - timedelta(seconds=1)
        task.cancel_requested = cancelled
        op = ProviderOperation(task_id=task_id, operation_key='split:0', provider='runninghub',
            request_fingerprint='f', provider_task_id='accepted', submission_status='submitted',
            slot_reserved_at=now, reconciliation_evidence={'accepted_at': (now-timedelta(minutes=16)).isoformat()})
        db.add(op)
        db.flush()
        reserve_provider_slot(db, 'runninghub', op.id, capacity=2)
        db.commit()
    calls = []
    async def submit(*args, **kwargs):
        pytest.fail('Recovery must not resubmit')
    async def collect(self, task_id):
        calls.append(task_id)
        return [RunningHubOutput('top.jpg', 'image/jpeg', jpeg_bytes(), 'https://test/top.jpg')]
    monkeypatch.setattr(RunningHubClient, 'submit_garment_recognition', submit)
    monkeypatch.setattr(RunningHubClient, 'collect_garment_recognition', collect)
    worker = TaskWorker(get_session_factory(), get_settings().model_copy(update={'workflow_provider': 'runninghub'}))
    assert anyio.run(worker.run_once)
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, task_id)
        assert task.error_code == 'provider_recovery_timeout'
        assert task.next_attempt_at is None
        op = db.scalar(select(ProviderOperation).where(ProviderOperation.task_id == task_id))
        assert op.slot_released_at is None
    assert calls == []
    reconciled = client.post(f'/v2/tasks/{task_id}/reconcile', headers=headers, json={})
    assert reconciled.status_code == 202
    assert client.post(f'/v2/tasks/{task_id}/reconcile', headers=headers, json={}).status_code == 429
    assert anyio.run(worker.run_once)
    assert calls == ['accepted']
    with get_session_factory()() as db:
        assert db.get(AnalysisTask, task_id).status == ('cancelled' if cancelled else 'succeeded')
        op = db.scalar(select(ProviderOperation).where(ProviderOperation.task_id == task_id, ProviderOperation.operation_key == 'split:0'))
        assert op.slot_released_at is not None


def test_stale_worker_cannot_persist_remote_result(client, monkeypatch):
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, ProviderOperation, utc_now
    from app.task_worker import TaskWorker
    from app.runninghub import RunningHubClient, RunningHubOutput
    task_id, headers = create(client, 'split')
    async def submit(*args, **kwargs):
        return 'remote'
    async def collect(*args):
        with get_session_factory()() as db:
            task = db.get(AnalysisTask, task_id)
            task.lease_token = 'replacement-worker'
            task.lease_expires_at = utc_now() + timedelta(seconds=60)
            db.commit()
        return [RunningHubOutput('top.jpg', 'image/jpeg', jpeg_bytes(), 'https://test/top.jpg')]
    monkeypatch.setattr(RunningHubClient, 'submit_garment_recognition', submit)
    monkeypatch.setattr(RunningHubClient, 'collect_garment_recognition', collect)
    worker = TaskWorker(get_session_factory(), get_settings().model_copy(update={'workflow_provider': 'runninghub'}))
    assert anyio.run(worker.run_once)
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, task_id)
        op = db.scalar(select(ProviderOperation).where(ProviderOperation.task_id == task_id))
        assert task.lease_token == 'replacement-worker'
        assert not op.result_data
        assert op.submission_status == 'submitted'
        assert op.slot_released_at is None

def test_runninghub_tagging_resumes_known_workflow(client, monkeypatch):
    import json
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, ProviderOperation, utc_now
    from app.task_worker import TaskWorker
    from app.runninghub import RunningHubClient, RunningHubWorkflow
    task_id, headers = create(client)
    counts = {'submit': 0, 'query': 0}
    monkeypatch.setattr(RunningHubWorkflow, 'from_file', lambda path: RunningHubWorkflow('tags', 'wf', {}))
    async def submit(*args, **kwargs):
        counts['submit'] += 1
        return 'tags-id'
    async def collect(self, remote_id):
        counts['query'] += 1
        assert remote_id == 'tags-id'
        if counts['query'] == 1:
            raise TimeoutError()
        return {'status': 'SUCCESS', 'data': {'text': json.dumps({'category': 'top', 'main_color': 'white', 'style': 'casual', 'season': 'summer', 'confidence': .9})}}
    monkeypatch.setattr(RunningHubClient, 'submit_workflow', submit)
    monkeypatch.setattr(RunningHubClient, 'collect_workflow', collect)
    settings = get_settings().model_copy(update={'garment_ai_provider': 'runninghub', 'ai_demo_mode': False})
    worker = TaskWorker(get_session_factory(), settings)
    assert anyio.run(worker.run_once)
    assert not anyio.run(worker.run_once)
    with get_session_factory()() as db:
        op = db.scalar(select(ProviderOperation).where(ProviderOperation.task_id == task_id))
        assert op.provider_task_id == 'tags-id'
        task = db.get(AnalysisTask, task_id)
        task.next_attempt_at = utc_now() - timedelta(seconds=1)
        op.reconciliation_evidence = {**op.reconciliation_evidence, 'last_query_at': (utc_now()-timedelta(seconds=31)).isoformat()}
        db.commit()
    assert anyio.run(worker.run_once)
    assert counts == {'submit': 1, 'query': 2}
    with get_session_factory()() as db:
        assert db.get(AnalysisTask, task_id).status == 'succeeded'


@pytest.mark.parametrize('provider', ['runninghub', 'comfyui'])
def test_client_collect_uses_only_query_and_download(monkeypatch, provider):
    import httpx
    from app.config import Settings
    from app.runninghub import RunningHubClient
    from app.comfyui import ComfyUIClient
    from app.provider_recovery import ProviderPending, ProviderTerminalFailure
    requests = []
    async def send(self, request, **kwargs):
        requests.append((request.method, request.url.path))
        assert request.url.path not in {'/prompt', '/upload/image', '/task/openapi/create', '/task/openapi/ai-app/run'}
        if request.url.path == '/task/openapi/outputs':
            return httpx.Response(200, json={'code': 0, 'data': [{'fileUrl': 'https://test/top.jpg'}]}, request=request)
        if request.url.path == '/history/id':
            return httpx.Response(200, json={'id': {'outputs': {'1': {'images': [{'filename': 'top.jpg'}]}}}}, request=request)
        return httpx.Response(200, content=jpeg_bytes(), request=request)
    monkeypatch.setattr(httpx.AsyncClient, 'send', send)
    settings = Settings(runninghub_api_key='test', runninghub_base_url='https://test', comfyui_base_url='https://test')
    monkeypatch.setattr(ComfyUIClient, '_load_workflow', lambda self: {'1': {'class_type': 'SaveImage'}})
    cls = RunningHubClient if provider == 'runninghub' else ComfyUIClient
    result = anyio.run(cls(settings).collect_garment_recognition, 'id')
    assert len(result) == 1
    assert len(requests) == 2

@pytest.mark.parametrize('terminal_failure', [False, True])
def test_restart_recovers_stranded_known_id_and_releases_terminal_slot(client, monkeypatch, terminal_failure):
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, ProviderOperation, utc_now
    from app.task_worker import TaskWorker
    from app.runninghub import RunningHubClient, RunningHubOutput
    from app.provider_recovery import ProviderTerminalFailure
    task_id, headers = create(client, 'split')
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, task_id)
        task.status = 'running'
        task.stage = 'awaiting_resolution'
        task.next_attempt_at = None
        db.add(ProviderOperation(task_id=task_id, operation_key='split:0', provider='runninghub',
            request_fingerprint='f', provider_task_id='stranded', submission_status='submitted',
            slot_reserved_at=utc_now(), reconciliation_evidence={'accepted_at': utc_now().isoformat()}))
        db.commit()
    calls = []
    async def collect(self, remote_id):
        calls.append(remote_id)
        if terminal_failure:
            raise ProviderTerminalFailure('provider_confirmed_failure')
        return [RunningHubOutput('top.jpg', 'image/jpeg', jpeg_bytes(), 'https://test/top.jpg')]
    monkeypatch.setattr(RunningHubClient, 'collect_garment_recognition', collect)
    worker = TaskWorker(get_session_factory(), get_settings().model_copy(update={'workflow_provider': 'runninghub'}))
    assert anyio.run(worker.run_once)
    assert calls == ['stranded']
    with get_session_factory()() as db:
        assert db.get(AnalysisTask, task_id).status == ('failed' if terminal_failure else 'succeeded')
        op = db.scalar(select(ProviderOperation).where(ProviderOperation.task_id == task_id, ProviderOperation.operation_key == 'split:0'))
        assert op.slot_released_at is not None


def test_cancel_returns_immediately_but_queries_remote_terminal(client, monkeypatch):
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, ProviderOperation, Garment, utc_now
    from app.task_worker import TaskWorker
    from app.runninghub import RunningHubClient, RunningHubOutput
    task_id, headers = create(client, 'split')
    calls = []
    async def submit(*args, **kwargs):
        return 'cancel-remote'
    async def collect(self, remote_id):
        calls.append(remote_id)
        if len(calls) == 1:
            raise TimeoutError()
        return [RunningHubOutput('top.jpg', 'image/jpeg', jpeg_bytes(), 'https://test/top.jpg')]
    monkeypatch.setattr(RunningHubClient, 'submit_garment_recognition', submit)
    monkeypatch.setattr(RunningHubClient, 'collect_garment_recognition', collect)
    worker = TaskWorker(get_session_factory(), get_settings().model_copy(update={'workflow_provider': 'runninghub'}))
    assert anyio.run(worker.run_once)
    cancelled = client.post(f'/v2/tasks/{task_id}/cancel', headers=headers)
    assert cancelled.status_code == 200
    assert cancelled.json()['status'] == 'cancelled'
    with get_session_factory()() as db:
        op = db.scalar(select(ProviderOperation).where(ProviderOperation.task_id == task_id))
        assert op.slot_released_at is None
        op.reconciliation_evidence = {**op.reconciliation_evidence, 'last_query_at': (utc_now()-timedelta(seconds=31)).isoformat()}
        task = db.get(AnalysisTask, task_id)
        task.next_attempt_at = utc_now()-timedelta(seconds=1)
        db.commit()
    assert anyio.run(worker.run_once)
    with get_session_factory()() as db:
        op = db.scalar(select(ProviderOperation).where(ProviderOperation.task_id == task_id))
        assert op.slot_released_at is not None
        assert db.get(AnalysisTask, task_id).status == 'cancelled'
        assert not list(db.scalars(select(Garment)))
    assert calls == ['cancel-remote', 'cancel-remote']


def test_cancel_during_split_tag_discards_late_garment(client, monkeypatch):
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, Garment
    from app.task_worker import TaskWorker
    from app.ai import AiService
    task_id, headers = create(client, 'split')
    original = AiService.analyze_garment
    async def cancel_during_tag(self, *args, **kwargs):
        with get_session_factory()() as db:
            task = db.get(AnalysisTask, task_id)
            task.cancel_requested = True
            task.status = 'cancelled'
            db.commit()
        return await original(self, *args, **kwargs)
    monkeypatch.setattr(AiService, 'analyze_garment', cancel_during_tag)
    assert anyio.run(TaskWorker(get_session_factory(), get_settings()).run_once)
    with get_session_factory()() as db:
        assert db.get(AnalysisTask, task_id).status == 'cancelled'
        assert not list(db.scalars(select(Garment)))
