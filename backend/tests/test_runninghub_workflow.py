import anyio
import pytest

import app.runninghub as runninghub_module
from app.config import Settings
from app.runninghub import RunningHubClient, RunningHubWorkflow


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class FakeAsyncClient:
    calls: list[dict[str, object]] = []
    responses: list[dict[str, object]] = []

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"url": url, **kwargs})
        return FakeResponse(self.responses.pop(0))


def test_runninghub_uploads_file_and_returns_comfyui_filename(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncClient.calls.clear()
    FakeAsyncClient.responses = [
        {
            "code": 200,
            "message": "success",
            "data": {"filename": "openapi/image.png", "download_url": "https://example.com/image.png"},
        }
    ]
    monkeypatch.setattr(runninghub_module.httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(runninghub_api_key="rh-key", runninghub_base_url="https://www.runninghub.cn")

    async def run_upload() -> str:
        client = RunningHubClient(settings)
        return await client.upload_file("shirt.png", "image/png", b"image")

    file_name = anyio.run(run_upload, backend="asyncio")

    call = FakeAsyncClient.calls[0]
    assert file_name == "openapi/image.png"
    assert call["url"] == "https://www.runninghub.cn/openapi/v2/media/upload/binary"
    assert call["headers"]["Authorization"] == "Bearer rh-key"
    assert call["files"]["file"] == ("shirt.png", b"image", "image/png")


def test_runninghub_create_task_builds_node_info_list(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncClient.calls.clear()
    FakeAsyncClient.responses = [
        {
            "code": 0,
            "msg": "success",
            "data": {"taskId": "task-123", "taskStatus": "RUNNING"},
        }
    ]
    monkeypatch.setattr(runninghub_module.httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(runninghub_api_key="rh-key", runninghub_base_url="https://www.runninghub.cn")
    workflow = RunningHubWorkflow.from_dict(
        {
            "name": "garment_recognition",
            "workflow_id": "workflow-123",
            "inputs": {
                "image": {"node_id": "14", "field_name": "image"},
                "prompt": {"node_id": "6", "field_name": "text"},
            },
            "task_options": {"retainSeconds": 60},
        }
    )

    async def run_task() -> str:
        client = RunningHubClient(settings)
        return await client.create_task(
            workflow,
            {"image": "openapi/image.png", "prompt": "Return JSON only"},
        )

    task_id = anyio.run(run_task, backend="asyncio")

    payload = FakeAsyncClient.calls[0]["json"]
    assert task_id == "task-123"
    assert FakeAsyncClient.calls[0]["url"] == "https://www.runninghub.cn/task/openapi/create"
    assert FakeAsyncClient.calls[0]["headers"]["Authorization"] == "Bearer rh-key"
    assert payload["apiKey"] == "rh-key"
    assert payload["workflowId"] == "workflow-123"
    assert payload["retainSeconds"] == 60
    assert payload["nodeInfoList"] == [
        {"nodeId": "14", "fieldName": "image", "fieldValue": "openapi/image.png"},
        {"nodeId": "6", "fieldName": "text", "fieldValue": "Return JSON only"},
    ]


def test_runninghub_run_workflow_uploads_files_creates_task_and_queries_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAsyncClient.calls.clear()
    FakeAsyncClient.responses = [
        {"code": 0, "message": "success", "data": {"fileName": "openapi/image.png"}},
        {"code": 0, "msg": "success", "data": {"taskId": "task-123"}},
        {"taskId": "task-123", "status": "RUNNING", "results": []},
        {
            "taskId": "task-123",
            "status": "SUCCESS",
            "results": [{"outputType": "json", "text": '{"category":"上衣"}'}],
        },
    ]
    monkeypatch.setattr(runninghub_module.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(runninghub_module.anyio, "sleep", lambda seconds: None)
    settings = Settings(
        runninghub_api_key="rh-key",
        runninghub_base_url="https://www.runninghub.cn",
        runninghub_poll_interval_seconds=0,
    )
    workflow = RunningHubWorkflow.from_dict(
        {
            "name": "garment_recognition",
            "workflow_id": "workflow-123",
            "inputs": {"image": {"node_id": "14", "field_name": "image"}},
        }
    )

    async def run_workflow() -> dict[str, object]:
        client = RunningHubClient(settings)
        return await client.run_workflow(
            workflow,
            files={"image": ("shirt.png", "image/png", b"image")},
        )

    result = anyio.run(run_workflow, backend="asyncio")

    assert [call["url"] for call in FakeAsyncClient.calls] == [
        "https://www.runninghub.cn/openapi/v2/media/upload/binary",
        "https://www.runninghub.cn/task/openapi/create",
        "https://www.runninghub.cn/openapi/v2/query",
        "https://www.runninghub.cn/openapi/v2/query",
    ]
    assert result["status"] == "SUCCESS"
    assert result["results"] == [{"outputType": "json", "text": '{"category":"上衣"}'}]


def test_runninghub_workflow_requires_workflow_id() -> None:
    with pytest.raises(ValueError, match="workflow_id"):
        RunningHubWorkflow.from_dict(
            {
                "name": "garment_recognition",
                "workflow_id": "",
                "inputs": {"image": {"node_id": "14", "field_name": "image"}},
            }
        )
