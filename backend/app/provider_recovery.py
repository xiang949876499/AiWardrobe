"""Read-only recovery policy shared by task routes and the worker."""
from datetime import datetime, timezone


class ProviderPending(RuntimeError):
    pass


class ProviderTerminalFailure(ValueError):
    pass


class LostTaskLease(RuntimeError):
    pass


def recovery_time(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def queryable(operation):
    return bool(operation.provider_task_id and operation.provider in {"runninghub", "comfyui"})
