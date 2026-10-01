import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select

from conftest import jpeg_bytes, login


@pytest.fixture(autouse=True)
def no_compatibility_wait(monkeypatch):
    try:
        from app import legacy_tasks
    except ImportError:
        return
    monkeypatch.setattr(legacy_tasks, "SYNC_WAIT_SECONDS", 0)


@pytest.mark.parametrize("path,kind", [
    ("/purchase/analyze-image", "purchase_image"),
    ("/garments/upload", "garment_single"),
    ("/uploads/plain-garment", "garment_single"),
    ("/uploads/garment-photo", "garment_split"),
])
def test_legacy_creation_queues_and_replays_without_inline_provider(client, monkeypatch, path, kind):
    from app.ai import AiService
    from app.database import get_session_factory
    from app.models import AnalysisTask, ProviderOperation

    async def forbidden(*args, **kwargs):
        raise AssertionError("API must not execute the provider inline")

    monkeypatch.setattr(AiService, "analyze_garment", forbidden)
    monkeypatch.setattr(AiService, "detect_garments", forbidden)
    headers = {"Authorization": f"Bearer {login(client)}"}
    first = client.post(path, headers=headers, files={"file": ("top.jpg", jpeg_bytes(), "image/jpeg")})
    assert first.status_code == 503
    second = client.post(path, headers=headers, files={"file": ("top.jpg", jpeg_bytes(), "image/jpeg")})
    assert second.status_code == 503
    assert first.json()["detail"]["task_id"] == second.json()["detail"]["task_id"]
    with get_session_factory()() as db:
        tasks = list(db.scalars(select(AnalysisTask)))
        assert len(tasks) == 1 and tasks[0].kind == kind
        assert list(db.scalars(select(ProviderOperation))) == []


def test_explicit_key_conflict_and_completed_replay(client):
    from app.config import get_settings
    from app.database import get_session_factory
    from app.task_worker import TaskWorker

    headers = {"Authorization": f"Bearer {login(client)}", "Idempotency-Key": str(uuid4())}
    path = "/garments/upload"
    submitted = client.post(path, headers=headers, files={"file": ("top.jpg", jpeg_bytes(), "image/jpeg")})
    assert submitted.status_code == 503
    conflict = client.post(path, headers=headers, files={"file": ("top.jpg", jpeg_bytes(110, 110), "image/jpeg")})
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
    assert asyncio.run(TaskWorker(get_session_factory(), get_settings()).run_once())
    ready = client.post(path, headers=headers, files={"file": ("top.jpg", jpeg_bytes(), "image/jpeg")})
    assert ready.status_code == 201
    again = client.post(path, headers=headers, files={"file": ("top.jpg", jpeg_bytes(), "image/jpeg")})
    assert again.json()["id"] == ready.json()["id"]
    assert ready.json()["review_status"] == "pending_review"


def test_legacy_url_creates_task_without_extraction(client, monkeypatch):
    from app import product_extraction

    async def forbidden(*args, **kwargs):
        raise AssertionError("API must not download URL before queuing")

    monkeypatch.setattr(product_extraction, "extract_product_image", forbidden)
    headers = {"Authorization": f"Bearer {login(client)}"}
    response = client.post("/purchase/analyze", headers=headers, json={"url": "https://shop.example.com/item/1"})
    assert response.status_code == 503
    assert response.json()["detail"]["task_id"]


def test_legacy_batch_queues_per_item_and_replays(client):
    headers = {"Authorization": f"Bearer {login(client)}", "Idempotency-Key": str(uuid4())}
    files = [("files", ("one.jpg", jpeg_bytes(), "image/jpeg")), ("files", ("two.jpg", jpeg_bytes(120, 120), "image/jpeg"))]
    first = client.post("/garments/batch-upload", headers=headers, files=files)
    assert first.status_code == 503
    second = client.post("/garments/batch-upload", headers=headers, files=files)
    assert second.json()["detail"]["task_ids"] == first.json()["detail"]["task_ids"]
    changed = client.post("/garments/batch-upload", headers=headers, files=files[:1])
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "idempotency_conflict"


@pytest.mark.parametrize("age_hours,status,ambiguous", [(1, "succeeded", False), (49, "failed", True)])
def test_keyless_replay_crosses_midnight_and_retains_old_unknown(client, age_hours, status, ambiguous):
    from datetime import datetime, timedelta, timezone
    from app.database import get_session_factory
    from app.models import AnalysisTask, ProviderOperation

    headers = {"Authorization": f"Bearer {login(client)}"}
    files = {"file": ("top.jpg", jpeg_bytes(), "image/jpeg")}
    first = client.post("/garments/upload", headers=headers, files=files)
    task_id = first.json()["detail"]["task_id"]
    with get_session_factory()() as db:
        task = db.get(AnalysisTask, task_id)
        task.idempotency_key = str(uuid4())  # A prior day's server-generated key.
        task.created_at = datetime.now(timezone.utc) - timedelta(hours=age_hours)
        task.status = status
        if ambiguous:
            db.add(ProviderOperation(task_id=task.id, operation_key="tag:0", provider="openai",
                                     request_fingerprint=task.input_fingerprint, submission_status="unknown"))
        db.commit()
    replay = client.post("/garments/upload", headers=headers, files=files)
    assert replay.json()["detail"]["task_id"] == task_id
    with get_session_factory()() as db:
        assert len(list(db.scalars(select(AnalysisTask)))) == 1


def test_legacy_paid_creation_uses_worker_submission_quota(client, monkeypatch):
    from app.ai import AiService
    from app.config import get_settings
    from app.database import get_session_factory
    from app.models import AnalysisTask, ProviderOperation
    from app.task_worker import TaskWorker

    settings = get_settings()
    settings.provider_hourly_submission_limit = 1
    settings.ai_demo_mode = False
    calls = []
    demo_settings = settings.model_copy(update={"ai_demo_mode": True})

    async def analyze(self, **kwargs):
        calls.append(kwargs)
        return await original(AiService(demo_settings), **kwargs)

    original = AiService.analyze_garment
    monkeypatch.setattr(AiService, "analyze_garment", analyze)
    headers = {"Authorization": f"Bearer {login(client)}"}
    for size in (96, 120):
        response = client.post("/garments/upload", headers=headers, files={"file": ("top.jpg", jpeg_bytes(size, size), "image/jpeg")})
        assert response.status_code == 503
        asyncio.run(TaskWorker(get_session_factory(), settings).run_once())
    assert len(calls) == 1
    with get_session_factory()() as db:
        assert len(list(db.scalars(select(AnalysisTask)))) == 2
        operations = list(db.scalars(select(ProviderOperation)))
        assert sum(operation.submission_status == "completed" for operation in operations) == 1
        waiting = list(db.scalars(select(AnalysisTask).where(AnalysisTask.status == "queued")))
        assert len(waiting) == 1
        assert waiting[0].next_attempt_at is not None
        assert waiting[0].lease_token is None
        assert not any(operation.task_id == waiting[0].id for operation in operations)


def test_shopping_auto_and_manual_analysis_only_queue_tasks(client, monkeypatch):
    from app.ai import AiService
    from app.database import get_session_factory
    from app.models import AnalysisTask, ProviderOperation

    async def forbidden(*args, **kwargs):
        raise AssertionError("recommendations must not execute providers in API")

    monkeypatch.setattr(AiService, "analyze_garment", forbidden)
    headers = {"Authorization": f"Bearer {login(client)}"}
    run = client.post("/shopping/recommendations", headers=headers, json={"target": "basics", "refresh": True})
    assert run.status_code == 201
    item_id = run.json()["items"][0]["id"]
    response = client.post(f"/shopping/recommendations/items/{item_id}/analyze", headers=headers)
    assert response.status_code == 503
    with get_session_factory()() as db:
        assert len(list(db.scalars(select(AnalysisTask)))) == 3
        assert list(db.scalars(select(ProviderOperation))) == []


def test_sync_wait_is_bounded_even_if_setting_exceeds_twenty_seconds(monkeypatch):
    from types import SimpleNamespace
    from app import legacy_tasks

    elapsed = [0.0]

    async def sleep(seconds):
        elapsed[0] += seconds

    monkeypatch.setattr(legacy_tasks, "SYNC_WAIT_SECONDS", 100)
    monkeypatch.setattr(legacy_tasks.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(legacy_tasks.asyncio, "sleep", sleep)
    db = SimpleNamespace(rollback=lambda: None, refresh=lambda task: None)
    tasks = [SimpleNamespace(status="queued")]
    assert asyncio.run(legacy_tasks.wait_for_tasks(db, tasks)) == tasks
    assert elapsed[0] == pytest.approx(20.0)
