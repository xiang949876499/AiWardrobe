import os
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "aiwardrobe-test.db"
    uploads_path = tmp_path / "uploads"
    private_assets_path = tmp_path / "private-assets"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("STORAGE_DRIVER", "local")
    monkeypatch.setenv("LOCAL_UPLOAD_DIR", str(uploads_path))
    monkeypatch.setenv("LOCAL_PRIVATE_ASSET_DIR", str(private_assets_path))
    monkeypatch.setenv("JWT_SECRET", "test-secret-with-at-least-thirty-two-bytes")
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setenv("AI_DEMO_MODE", "true")
    monkeypatch.setenv("WORKFLOW_PROVIDER", "demo")
    os.environ.pop("AI_API_KEY", None)

    from app.config import get_settings
    from app.database import reset_database_connection

    get_settings.cache_clear()
    reset_database_connection()

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


def login(client: TestClient, email: str = "stylist@example.com") -> str:
    request = client.post("/auth/email-code/request", json={"email": email})
    assert request.status_code == 202
    code = request.json()["dev_code"]

    verify = client.post("/auth/email-code/verify", json={"email": email, "code": code})
    assert verify.status_code == 200
    return verify.json()["access_token"]


def jpeg_bytes(width: int = 96, height: int = 96) -> bytes:
    from PIL import Image

    output = BytesIO()
    Image.new("RGB", (width, height), (235, 230, 225)).save(output, format="JPEG")
    return output.getvalue()


@pytest.fixture()
def legacy_worker(monkeypatch):
    """Explicit worker harness for tests of legacy synchronous result envelopes.

    Production adapters only poll. These opted-in tests drive the actual worker
    (with its provider-operation/capacity checks) after task submission instead
    of relying on a provider call inside the API handler.
    """
    from app.config import get_settings
    from app.database import get_session_factory
    from app.task_worker import TaskWorker
    from app import shopping_recommendations
    from app.routers import garments, purchase, uploads

    async def complete(db, tasks):
        worker = TaskWorker(get_session_factory(), get_settings())
        for _ in range(50):
            db.rollback()
            for task in tasks:
                db.refresh(task)
            if all(task.status in {"succeeded", "failed", "cancelled"} for task in tasks):
                break
            if not await worker.run_once():
                break
        db.rollback()
        for task in tasks:
            db.refresh(task)
        return tasks

    for module in (garments, purchase, uploads, shopping_recommendations):
        monkeypatch.setattr(module, "wait_for_tasks", complete)

    original_recommendation_analysis = shopping_recommendations.analyze_recommendation_item

    async def recommendation_analysis(db, user, settings, item, *args, **kwargs):
        result = await original_recommendation_analysis(db, user, settings, item, *args, **kwargs)
        if kwargs.get("wait") is False:
            from sqlalchemy import select
            from app.models import AnalysisTask
            tasks = list(db.scalars(select(AnalysisTask).where(AnalysisTask.user_id == user.id,
                                                             AnalysisTask.status == "queued")))
            if tasks:
                await complete(db, tasks)
            db.refresh(result)
        return result

    monkeypatch.setattr(shopping_recommendations, "analyze_recommendation_item", recommendation_analysis)
