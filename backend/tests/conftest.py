import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    db_path = tmp_path / "aiwardrobe-test.db"
    uploads_path = tmp_path / "uploads"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("STORAGE_DRIVER", "local")
    monkeypatch.setenv("LOCAL_UPLOAD_DIR", str(uploads_path))
    monkeypatch.setenv("JWT_SECRET", "test-secret-with-at-least-thirty-two-bytes")
    monkeypatch.setenv("TESTING", "true")
    monkeypatch.setenv("AI_DEMO_MODE", "true")
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
