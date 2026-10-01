import json
from pathlib import Path
import subprocess
import sys

import pytest

from app import local_launcher


@pytest.fixture
def child():
    process = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(60)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **local_launcher.process_options())
    try:
        yield process
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=10)


def _manifest(root: Path, records: dict) -> Path:
    folder = root / ".local-services"
    folder.mkdir()
    path = folder / "pids.json"
    path.write_text(json.dumps(records), encoding="utf-8")
    return path


def test_stop_local_terminates_only_recorded_matching_process(tmp_path, child):
    path = _manifest(tmp_path, {"worker": local_launcher.process_record(child)})
    assert local_launcher.stop_local(tmp_path) == ["worker"]
    child.wait(timeout=10)
    assert json.loads(path.read_text(encoding="utf-8")) == {}


def test_stop_local_refuses_reused_process_id(tmp_path, child):
    record = local_launcher.process_record(child)
    record["identity"] = "different-process-creation-time"
    path = _manifest(tmp_path, {"worker": record})
    with pytest.raises(RuntimeError, match="identity"):
        local_launcher.stop_local(tmp_path)
    assert child.poll() is None
    assert json.loads(path.read_text(encoding="utf-8"))["worker"] == record


def test_stop_local_rejects_unrecognized_service(tmp_path, child):
    _manifest(tmp_path, {"unrelated": local_launcher.process_record(child)})
    with pytest.raises(RuntimeError, match="service"):
        local_launcher.stop_local(tmp_path)
    assert child.poll() is None


def test_launch_refuses_existing_service_before_starting_another(tmp_path, child, monkeypatch):
    from types import SimpleNamespace

    _manifest(tmp_path, {"worker": local_launcher.process_record(child)})
    monkeypatch.setattr(local_launcher, "local_environment", lambda root: {})
    monkeypatch.setattr(local_launcher.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0))
    starts = []
    def unexpected_start(*args, **kwargs):
        starts.append(args)
        raise AssertionError("Duplicate worker was started")
    monkeypatch.setattr(local_launcher.subprocess, "Popen", unexpected_start)
    with pytest.raises(RuntimeError, match="already"):
        local_launcher.launch(tmp_path, "worker")
    assert starts == []
