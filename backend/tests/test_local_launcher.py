from pathlib import Path


def test_default_launcher_uses_managed_api_worker_and_hidden_processes():
    root = Path(__file__).resolve().parents[2]
    script = (root / "start.bat").read_text()
    assert "app.local_launcher local" in script
    assert "app.local_launcher worker" in script
    from app.local_launcher import service_commands, process_options

    commands = service_commands(root, "local")
    assert set(commands) == {"api", "worker", "wedding-worker", "frontend"}
    assert commands["worker"][0][-2:] == ["-m", "app.task_worker"]
    assert commands["api"][1] == commands["worker"][1] == root / "backend"
    assert process_options("win32")["creationflags"] != 0


def test_launcher_environment_shared_and_preserves_provider_configuration(tmp_path, monkeypatch):
    from app.local_launcher import local_environment

    monkeypatch.chdir(tmp_path)
    source = {"DATABASE_URL": "sqlite:///custom.db", "JWT_SECRET": "custom-dev-secret",
              "LOCAL_PRIVATE_ASSET_DIR": "private-custom", "STORAGE_DRIVER": "local",
              "AI_API_KEY": "private-provider-value", "AI_DEMO_MODE": "false", "WORKFLOW_PROVIDER": "runninghub"}
    result = local_environment(tmp_path, source)
    for key, value in source.items():
        assert result[key] == value
    assert result["ENVIRONMENT"] == "development"


def test_worker_startup_exit_is_reported(tmp_path):
    import pytest
    from app.local_launcher import validate_started

    class Exited:
        def poll(self):
            return 1

    with pytest.raises(RuntimeError, match="worker.*exited.*worker.log"):
        validate_started({"worker": Exited()}, tmp_path)


def test_independent_local_worker_completes_upload_with_shared_config(client, tmp_path):
    import os
    import subprocess
    import time
    from conftest import jpeg_bytes, login
    from app.local_launcher import process_options, service_commands

    root = Path(__file__).resolve().parents[2]
    headers = {"Authorization": f"Bearer {login(client)}", "Idempotency-Key": "11111111-2222-4333-8444-555555555555"}
    queued = client.post("/v2/tasks/garment-upload", headers=headers,
                         files={"file": ("local-worker.jpg", jpeg_bytes(), "image/jpeg")})
    assert queued.status_code == 202
    command, directory = service_commands(root, "worker")["worker"]
    with (tmp_path / "worker.log").open("wb") as log:
        process = subprocess.Popen(command, cwd=directory, env=dict(os.environ),
                                   stdout=log, stderr=subprocess.STDOUT, **process_options())
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                state = client.get(f"/v2/tasks/{queued.json()['id']}", headers=headers).json()
                if state["status"] in {"succeeded", "failed"}:
                    break
                assert process.poll() is None
                time.sleep(0.1)
            assert state["status"] == "succeeded", state
            garment_id = state["result_refs"][0]["id"]
            garment = client.get(f"/garments/{garment_id}", headers=headers).json()
            assert garment["status"] == "ready"
            assert garment["review_status"] == "pending_review"
            assert garment["user_confirmed_at"] is None
            assert client.get(garment["image_url"]).status_code == 200
        finally:
            process.terminate()
            process.wait(timeout=10)
