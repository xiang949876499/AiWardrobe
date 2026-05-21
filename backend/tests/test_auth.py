from fastapi.testclient import TestClient


def test_development_without_smtp_returns_dev_code(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'dev.db'}")
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("TESTING", "false")
    monkeypatch.setenv("JWT_SECRET", "dev-secret-with-at-least-thirty-two-bytes")
    monkeypatch.delenv("SMTP_HOST", raising=False)

    from app.config import get_settings
    from app.database import reset_database_connection
    from app.main import create_app

    get_settings.cache_clear()
    reset_database_connection()

    with TestClient(create_app()) as client:
        requested = client.post("/auth/email-code/request", json={"email": "dev@example.com"})

    assert requested.status_code == 202
    assert len(requested.json()["dev_code"]) == 6


def test_register_then_login_with_email_and_password(client: TestClient) -> None:
    registered = client.post(
        "/auth/register",
        json={"email": "new@example.com", "password": "correct-horse-123"},
    )

    assert registered.status_code == 201
    assert registered.json()["access_token"]
    assert registered.json()["user"]["email"] == "new@example.com"

    logged_in = client.post(
        "/auth/login",
        json={"email": "new@example.com", "password": "correct-horse-123"},
    )

    assert logged_in.status_code == 200
    assert logged_in.json()["access_token"]
    assert logged_in.json()["user"]["email"] == "new@example.com"


def test_login_rejects_wrong_password(client: TestClient) -> None:
    client.post(
        "/auth/register",
        json={"email": "new@example.com", "password": "correct-horse-123"},
    )

    logged_in = client.post(
        "/auth/login",
        json={"email": "new@example.com", "password": "wrong-password"},
    )

    assert logged_in.status_code == 400
    assert logged_in.json()["detail"] == "Invalid email or password"


def test_register_rejects_existing_email(client: TestClient) -> None:
    payload = {"email": "new@example.com", "password": "correct-horse-123"}
    client.post("/auth/register", json=payload)

    duplicate = client.post("/auth/register", json=payload)

    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Email already registered"


def test_email_code_login_returns_token_and_user(client: TestClient) -> None:
    requested = client.post("/auth/email-code/request", json={"email": "user@example.com"})

    assert requested.status_code == 202
    assert len(requested.json()["dev_code"]) == 6

    verified = client.post(
        "/auth/email-code/verify",
        json={"email": "user@example.com", "code": requested.json()["dev_code"]},
    )

    assert verified.status_code == 200
    body = verified.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == "user@example.com"


def test_email_code_login_rejects_wrong_code(client: TestClient) -> None:
    client.post("/auth/email-code/request", json={"email": "user@example.com"})

    verified = client.post(
        "/auth/email-code/verify",
        json={"email": "user@example.com", "code": "000000"},
    )

    assert verified.status_code == 400
    assert verified.json()["detail"] == "Invalid or expired verification code"


def test_email_code_login_uses_latest_code_when_multiple_are_requested(client: TestClient) -> None:
    client.post("/auth/email-code/request", json={"email": "repeat@example.com"})
    latest = client.post("/auth/email-code/request", json={"email": "repeat@example.com"}).json()["dev_code"]

    verified = client.post(
        "/auth/email-code/verify",
        json={"email": "repeat@example.com", "code": latest},
    )

    assert verified.status_code == 200
    assert verified.json()["user"]["email"] == "repeat@example.com"
