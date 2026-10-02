"""Launch independent local services with one configuration and hidden logs."""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import subprocess
import sys
import time

from app.config import Settings


@contextmanager
def _windows_process(pid: int):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        yield None
        return
    try:
        creation, exit_time, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle, creation, exit_time, system, user):
            raise RuntimeError(f"Cannot verify process identity for PID {pid}")
        yield handle, str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


def process_record(process: subprocess.Popen) -> dict[str, object]:
    if sys.platform == "win32":
        with _windows_process(process.pid) as identity:
            if identity is None:
                raise RuntimeError("Service exited before its identity could be recorded")
            return {"pid": process.pid, "identity": identity[1]}
    fields = Path(f"/proc/{process.pid}/stat").read_text().rsplit(")", 1)[1].split()
    return {"pid": process.pid, "identity": fields[19]}


def _stop_record(record: dict[str, object]) -> None:
    pid = record.get("pid")
    expected = record.get("identity")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not isinstance(expected, str):
        raise RuntimeError("Invalid service process identity record")
    if sys.platform == "win32":
        # Keep the verified process handle alive throughout taskkill, so this PID
        # cannot be recycled for an unrelated process between check and stop.
        with _windows_process(pid) as identity:
            if identity is None:
                return
            if identity[1] != expected:
                raise RuntimeError(f"Refusing to stop PID {pid}: process identity changed")
            result = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, timeout=15, **process_options())
            if result.returncode:
                raise RuntimeError(f"Failed to stop verified service PID {pid}")
    else:
        import signal

        path = Path(f"/proc/{pid}/stat")
        if not path.exists():
            return
        actual = path.read_text().rsplit(")", 1)[1].split()[19]
        if actual != expected or os.getpgid(pid) != pid:
            raise RuntimeError(f"Refusing to stop PID {pid}: process identity changed")
        os.killpg(pid, signal.SIGTERM)


def stop_local(root: Path) -> list[str]:
    path = root / ".local-services" / "pids.json"
    if not path.exists():
        return []
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, dict) or set(records) - {"api", "worker", "wedding-worker", "frontend"}:
        raise RuntimeError("Invalid local service manifest")
    stopped = []
    for name, record in list(records.items()):
        if not isinstance(record, dict):
            raise RuntimeError("Legacy manifest lacks process identity; inspect PIDs manually")
        _stop_record(record)
        stopped.append(name)
        del records[name]
        path.write_text(json.dumps(records), encoding="utf-8")
    return stopped


def local_environment(root: Path, source: dict[str, str] | None = None) -> dict[str, str]:
    environment = dict(os.environ if source is None else source)
    defaults = {
        "DATABASE_URL": "sqlite:///./aiwardrobe-local.db", "ENVIRONMENT": "development",
        "FRONTEND_ORIGIN": "http://localhost:5174", "STORAGE_DRIVER": "local",
        "LOCAL_UPLOAD_DIR": "uploads", "LOCAL_PRIVATE_ASSET_DIR": "private-assets",
        "JWT_SECRET": "local-development-secret-with-at-least-thirty-two-bytes",
    }
    # Resolve .env once, then export exactly the same full settings to API/worker.
    configured = Settings(_env_file=(root / ".env", root / "backend" / ".env"))
    declared = {name.upper() for name in configured.model_fields_set}
    for name, value in defaults.items():
        if name not in environment and name not in declared:
            environment[name] = value
    for name, value in configured.model_dump().items():
        variable = name.upper()
        if value is not None and variable not in environment:
            environment[variable] = str(value).lower() if isinstance(value, bool) else str(value)
    environment["VITE_BACKEND_TARGET"] = "http://127.0.0.1:8031"
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def process_options(platform: str = sys.platform) -> dict:
    if platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {"start_new_session": True}


def service_commands(root: Path, mode: str) -> dict[str, tuple[list[str], Path]]:
    backend = root / "backend"
    commands = {
        "api": ([sys.executable, "-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "8031"], backend),
        "worker": ([sys.executable, "-m", "app.task_worker"], backend),
        "wedding-worker": ([sys.executable, "-m", "app.wedding_worker", "--poll-seconds", "2"], backend),
        "frontend": ([shutil.which("npm.cmd") or shutil.which("npm") or "npm", "run", "dev", "--", "--host", "127.0.0.1", "--port", "5174", "--strictPort"], root / "frontend"),
    }
    if mode == "local":
        return commands
    return {"api" if mode == "backend" else mode: commands["api" if mode == "backend" else mode]}


def validate_started(processes: dict[str, subprocess.Popen], log_dir: Path) -> None:
    for name, process in processes.items():
        code = process.poll()
        if code is not None:
            raise RuntimeError(f"{name} exited with code {code}; see {log_dir / (name + '.log')}")


def launch(root: Path, mode: str) -> None:
    environment = local_environment(root)
    commands = service_commands(root, mode)
    log_dir = root / ".local-services"
    path = log_dir / "pids.json"
    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(records, dict):
        raise RuntimeError("Invalid local service manifest")
    if set(commands) & set(records):
        raise RuntimeError("Service already recorded; run start.bat stop-local first")
    backend = root / "backend"
    if set(commands) & {"api", "worker", "wedding-worker"}:
        # Imports + DB initialization must succeed before either consumer starts.
        check = subprocess.run([sys.executable, "-c", "from app.main import create_app; from app.task_worker import TaskWorker; import app.wedding_worker; from app.database import init_db; create_app(); init_db()"],
                               cwd=backend, env=environment, capture_output=True, text=True, **process_options())
        if check.returncode:
            raise RuntimeError("Backend/worker preflight failed: " + check.stderr[-2000:])
    log_dir.mkdir(exist_ok=True)
    processes = {}
    try:
        for name, (command, directory) in commands.items():
            with (log_dir / f"{name}.log").open("ab") as log:
                # Windows npm.cmd needs cmd.exe; no shell-built environment or
                # configuration is passed, and subprocess quotes each argument.
                if sys.platform == "win32" and command[0].lower().endswith(".cmd"):
                    command = [os.environ.get("COMSPEC", "cmd.exe"), "/c", *command]
                processes[name] = subprocess.Popen(command, cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                                                    stdout=log, stderr=subprocess.STDOUT, **process_options())
        time.sleep(3)
        validate_started(processes, log_dir)
        records.update({name: process_record(process) for name, process in processes.items()})
        path.write_text(json.dumps(records), encoding="utf-8")
    except Exception:
        for process in processes.values():
            if process.poll() is None:
                try:
                    _stop_record(process_record(process))
                except Exception:
                    process.terminate()
        raise
    print(f"Started {', '.join(processes)}; logs and PIDs: {log_dir}. Stop with start.bat stop-local.")


def main() -> int:
    mode = sys.argv[1] if len(sys.argv) > 1 else "local"
    if mode not in {"local", "backend", "worker", "wedding-worker", "frontend", "stop-local"}:
        print("Unknown launcher mode", file=sys.stderr)
        return 1
    try:
        root = Path(__file__).resolve().parents[2]
        if mode == "stop-local":
            print("Stopped: " + ", ".join(stop_local(root)))
        else:
            launch(root, mode)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
