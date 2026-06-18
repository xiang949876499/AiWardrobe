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

    async def get(self, url: str, **kwargs) -> "FakeBinaryResponse":
        self.calls.append({"url": url, "method": "GET", **kwargs})
        if self.responses:
            return FakeResponse(self.responses.pop(0))
        return FakeBinaryResponse(b"image-output")


class FakeBinaryResponse:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def raise_for_status(self) -> None:
        return None


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


def test_runninghub_garment_recognition_downloads_output_images(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    FakeAsyncClient.calls.clear()
    FakeAsyncClient.responses = [
        {"code": 0, "message": "success", "data": {"fileName": "openapi/image.png"}},
        {"code": 0, "msg": "success", "data": {"taskId": "task-123"}},
        {
            "taskId": "task-123",
            "status": "SUCCESS",
            "data": [
                {
                    "fileUrl": "https://example.com/output/top.png",
                    "fileType": "png",
                    "nodeId": "139",
                }
            ],
        },
    ]
    monkeypatch.setattr(runninghub_module.httpx, "AsyncClient", FakeAsyncClient)
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        '{"workflow_id":"workflow-123","inputs":{"image":{"node_id":"78","field_name":"image"}}}',
        encoding="utf-8",
    )
    settings = Settings(
        runninghub_api_key="rh-key",
        runninghub_base_url="https://www.runninghub.cn",
        runninghub_poll_interval_seconds=0,
        runninghub_garment_workflow_file=str(workflow_path),
    )

    async def run_workflow():
        client = RunningHubClient(settings)
        return await client.run_garment_recognition("look.jpg", "image/jpeg", b"image")

    outputs = anyio.run(run_workflow, backend="asyncio")

    assert outputs[0].filename == "top.png"
    assert outputs[0].content_type == "image/png"
    assert outputs[0].data == b"image-output"
    assert outputs[0].file_url == "https://example.com/output/top.png"
    assert outputs[0].node_id == "139"
    assert [call["url"] for call in FakeAsyncClient.calls] == [
        "https://www.runninghub.cn/openapi/v2/media/upload/binary",
        "https://www.runninghub.cn/task/openapi/create",
        "https://www.runninghub.cn/task/openapi/outputs",
        "https://example.com/output/top.png",
    ]


def test_runninghub_ai_app_recognition_fetches_demo_nodes_and_downloads_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    FakeAsyncClient.calls.clear()
    FakeAsyncClient.responses = [
        {
            "code": 0,
            "msg": "success",
            "data": {
                "nodeInfoList": [
                    {
                        "nodeId": "39",
                        "fieldName": "image",
                        "fieldType": "IMAGE",
                        "description": "Upload image",
                    }
                ]
            },
        },
        {"code": 0, "message": "success", "data": {"fileName": "openapi/image.png"}},
        {"code": 0, "msg": "success", "data": {"taskId": "task-123", "taskStatus": "RUNNING"}},
        {"code": 0, "msg": "success", "data": [{"fileUrl": "https://example.com/output/top.png"}]},
    ]
    monkeypatch.setattr(runninghub_module.httpx, "AsyncClient", FakeAsyncClient)
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        '{"webapp_id":"2059472064133812225","inputs":{}}',
        encoding="utf-8",
    )
    settings = Settings(
        runninghub_api_key="rh-key",
        runninghub_base_url="https://www.runninghub.cn",
        runninghub_poll_interval_seconds=0,
        runninghub_garment_workflow_file=str(workflow_path),
    )

    async def run_workflow():
        client = RunningHubClient(settings)
        return await client.run_garment_recognition("look.jpg", "image/jpeg", b"image")

    outputs = anyio.run(run_workflow, backend="asyncio")

    assert outputs[0].filename == "top.png"
    assert outputs[0].data == b"image-output"
    assert FakeAsyncClient.calls[0]["url"] == (
        "https://www.runninghub.cn/api/webapp/apiCallDemo?apiKey=rh-key&webappId=2059472064133812225"
    )
    ai_app_payload = FakeAsyncClient.calls[2]["json"]
    assert FakeAsyncClient.calls[1]["url"] == "https://www.runninghub.cn/openapi/v2/media/upload/binary"
    assert FakeAsyncClient.calls[2]["url"] == "https://www.runninghub.cn/task/openapi/ai-app/run"
    assert ai_app_payload["webappId"] == "2059472064133812225"
    assert ai_app_payload["apiKey"] == "rh-key"
    assert ai_app_payload["nodeInfoList"] == [{"nodeId": "39", "fieldName": "image", "fieldValue": "openapi/image.png"}]


def test_runninghub_workflow_rejects_placeholder_configuration() -> None:
    with pytest.raises(ValueError, match="workflow_id"):
        RunningHubWorkflow.from_dict(
            {
                "workflow_id": "replace-with-runninghub-garment-recognition-workflow-id",
                "inputs": {"image": {"node_id": "78", "field_name": "image"}},
            }
        )
    with pytest.raises(ValueError, match="node_id"):
        RunningHubWorkflow.from_dict(
            {
                "workflow_id": "workflow-123",
                "inputs": {"image": {"node_id": "replace-with-load-image-node-id", "field_name": "image"}},
            }
        )


def test_runninghub_workflow_requires_workflow_id() -> None:
    with pytest.raises(ValueError, match="workflow_id"):
        RunningHubWorkflow.from_dict(
            {
                "name": "garment_recognition",
                "workflow_id": "",
                "inputs": {"image": {"node_id": "14", "field_name": "image"}},
            }
        )
