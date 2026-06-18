import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import anyio
import httpx

from app.config import Settings


class RunningHubError(RuntimeError):
    pass


@dataclass(frozen=True)
class RunningHubOutput:
    filename: str
    content_type: str
    data: bytes
    file_url: str
    node_id: str | None = None


@dataclass(frozen=True)
class RunningHubWorkflowInput:
    node_id: str
    field_name: str

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "RunningHubWorkflowInput":
        node_id = str(payload.get("node_id") or payload.get("nodeId") or "").strip()
        field_name = str(payload.get("field_name") or payload.get("fieldName") or "").strip()
        if not node_id or not field_name:
            raise ValueError("workflow input requires node_id and field_name")
        if "replace-with" in node_id:
            raise ValueError("workflow input node_id is still a placeholder")
        if "replace-with" in field_name:
            raise ValueError("workflow input field_name is still a placeholder")
        return cls(node_id=node_id, field_name=field_name)


@dataclass(frozen=True)
class RunningHubWorkflow:
    name: str
    workflow_id: str | None
    inputs: dict[str, RunningHubWorkflowInput]
    webapp_id: str | None = None
    access_password: str | None = None
    task_options: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str | Path) -> "RunningHubWorkflow":
        with Path(path).open("r", encoding="utf-8") as file:
            return cls.from_dict(json.load(file))

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "RunningHubWorkflow":
        workflow_id = str(payload.get("workflow_id") or payload.get("workflowId") or "").strip()
        webapp_id = str(payload.get("webapp_id") or payload.get("webappId") or "").strip()
        if not workflow_id and not webapp_id:
            raise ValueError("workflow_id or webapp_id is required")
        if "replace-with" in workflow_id:
            raise ValueError("workflow_id is still a placeholder")
        if "replace-with" in webapp_id:
            raise ValueError("webapp_id is still a placeholder")
        inputs_payload = payload.get("inputs") or {}
        if not isinstance(inputs_payload, dict):
            raise ValueError("workflow inputs are required")
        if workflow_id and not inputs_payload:
            raise ValueError("workflow inputs are required")
        task_options = payload.get("task_options") or payload.get("taskOptions") or {}
        if not isinstance(task_options, dict):
            raise ValueError("task_options must be an object")
        access_password = payload.get("access_password") or payload.get("accessPassword")
        return cls(
            name=str(payload.get("name") or "runninghub_workflow"),
            workflow_id=workflow_id or None,
            inputs={
                str(input_name): RunningHubWorkflowInput.from_dict(input_payload)
                for input_name, input_payload in inputs_payload.items()
                if isinstance(input_payload, dict)
            },
            webapp_id=webapp_id or None,
            access_password=str(access_password) if access_password else None,
            task_options=dict(task_options),
        )

    def build_node_info_list(self, values: dict[str, object]) -> list[dict[str, object]]:
        missing = [name for name in self.inputs if name not in values]
        if missing:
            raise ValueError(f"workflow inputs missing values: {', '.join(missing)}")
        return [
            {
                "nodeId": input_config.node_id,
                "fieldName": input_config.field_name,
                "fieldValue": values[input_name],
            }
            for input_name, input_config in self.inputs.items()
        ]


class RunningHubClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def upload_file(self, filename: str, content_type: str, data: bytes) -> str:
        payload = await self._post(
            "/openapi/v2/media/upload/binary",
            files={"file": (filename, data, content_type)},
        )
        response_data = self._require_success(payload).get("data") or {}
        if not isinstance(response_data, dict):
            raise RunningHubError("RunningHub upload returned invalid data")
        file_name = str(response_data.get("fileName") or response_data.get("filename") or "").strip()
        if not file_name:
            raise RunningHubError("RunningHub upload did not return fileName")
        return file_name

    async def create_task(self, workflow: RunningHubWorkflow, values: dict[str, object]) -> str:
        if not workflow.workflow_id:
            raise RunningHubError("workflow_id is required for ComfyUI workflow tasks")
        task_payload: dict[str, object] = {
            "apiKey": self._api_key(),
            "workflowId": workflow.workflow_id,
            **workflow.task_options,
            "nodeInfoList": workflow.build_node_info_list(values),
        }
        if workflow.access_password:
            task_payload["accessPassword"] = workflow.access_password

        payload = await self._post("/task/openapi/create", json=task_payload)
        response_data = self._require_success(payload).get("data") or {}
        if not isinstance(response_data, dict):
            raise RunningHubError("RunningHub task creation returned invalid data")
        task_id = str(response_data.get("taskId") or "").strip()
        if not task_id:
            raise RunningHubError("RunningHub task creation did not return taskId")
        return task_id

    async def create_ai_app_task(self, workflow: RunningHubWorkflow, values: dict[str, object]) -> str:
        if not workflow.webapp_id:
            raise RunningHubError("webapp_id is required for AI App tasks")
        task_payload: dict[str, object] = {
            "apiKey": self._api_key(),
            "webappId": workflow.webapp_id,
            **workflow.task_options,
            "nodeInfoList": workflow.build_node_info_list(values),
        }
        payload = await self._post("/task/openapi/ai-app/run", json=task_payload)
        response_data = self._require_success(payload).get("data") or {}
        if not isinstance(response_data, dict):
            raise RunningHubError("RunningHub AI App task creation returned invalid data")
        task_id = str(response_data.get("taskId") or "").strip()
        if not task_id:
            raise RunningHubError("RunningHub AI App task creation did not return taskId")
        return task_id

    async def query_task(self, task_id: str) -> dict[str, object]:
        payload = await self._post("/openapi/v2/query", json={"apiKey": self._api_key(), "taskId": task_id})
        if int(payload.get("code", 0) or 0) != 0:
            raise RunningHubError(str(payload.get("msg") or payload.get("message") or "RunningHub query failed"))
        return payload

    async def run_workflow(
        self,
        workflow: RunningHubWorkflow,
        inputs: dict[str, object] | None = None,
        files: dict[str, tuple[str, str, bytes]] | None = None,
    ) -> dict[str, object]:
        values = dict(inputs or {})
        for input_name, (filename, content_type, data) in (files or {}).items():
            values[input_name] = await self.upload_file(filename, content_type, data)

        task_id = await self.create_task(workflow, values)
        deadline = time.monotonic() + self.settings.runninghub_poll_timeout_seconds
        while True:
            result = await self.query_task(task_id)
            status = str(result.get("status") or "").upper()
            if status == "SUCCESS":
                return result
            if status in {"FAILED", "FAIL", "ERROR", "EXCEPTION"}:
                message = result.get("errorMessage") or result.get("msg") or "RunningHub task failed"
                raise RunningHubError(str(message))
            if time.monotonic() >= deadline:
                raise RunningHubError(f"RunningHub task timed out: {task_id}")
            if self.settings.runninghub_poll_interval_seconds > 0:
                await anyio.sleep(self.settings.runninghub_poll_interval_seconds)

    async def run_garment_recognition(self, filename: str, content_type: str, image_bytes: bytes) -> list[RunningHubOutput]:
        workflow = RunningHubWorkflow.from_file(self.settings.runninghub_garment_workflow_file)
        workflow = await self._with_ai_app_demo_inputs(workflow)
        image_input_name = self._image_input_name(workflow)
        values = {image_input_name: await self.upload_file(filename, content_type, image_bytes)}
        task_id = (
            await self.create_ai_app_task(workflow, values)
            if workflow.webapp_id
            else await self.create_task(workflow, values)
        )
        result = await self._wait_for_comfyui_outputs(task_id)
        output_refs = self._extract_output_refs(result)
        if not output_refs:
            raise RunningHubError("RunningHub workflow did not return garment images")
        return await self._download_outputs(output_refs)

    async def _with_ai_app_demo_inputs(self, workflow: RunningHubWorkflow) -> RunningHubWorkflow:
        if workflow.inputs or not workflow.webapp_id:
            return workflow
        demo = await self.get_ai_app_demo(workflow.webapp_id)
        image_input = _image_input_from_demo(demo)
        return RunningHubWorkflow(
            name=workflow.name,
            workflow_id=workflow.workflow_id,
            webapp_id=workflow.webapp_id,
            inputs={"image": image_input},
            access_password=workflow.access_password,
            task_options=workflow.task_options,
        )

    def _image_input_name(self, workflow: RunningHubWorkflow) -> str:
        if "image" in workflow.inputs:
            return "image"
        for input_name in workflow.inputs:
            return input_name
        raise RunningHubError("RunningHub workflow does not define an image input")

    async def get_ai_app_demo(self, webapp_id: str) -> dict[str, object]:
        query = urlencode({"apiKey": self._api_key(), "webappId": webapp_id})
        payload = await self._get(f"/api/webapp/apiCallDemo?{query}")
        return self._require_success(payload)

    async def _wait_for_comfyui_outputs(self, task_id: str) -> dict[str, object]:
        deadline = time.monotonic() + self.settings.runninghub_poll_timeout_seconds
        while True:
            payload = await self._post(
                "/task/openapi/outputs",
                json={"apiKey": self._api_key(), "taskId": task_id},
            )
            code = int(payload.get("code", 0) or 0)
            if code not in {0, 200}:
                message = str(payload.get("msg") or payload.get("message") or "RunningHub output query failed")
                if _looks_pending(message) and time.monotonic() < deadline:
                    await self._sleep_between_polls()
                    continue
                raise RunningHubError(message)
            data = payload.get("data")
            if isinstance(data, list) and data:
                return {"taskId": task_id, "status": "SUCCESS", "data": data, "results": data}
            if _looks_failed(payload):
                raise RunningHubError(str(payload.get("msg") or payload.get("message") or "RunningHub task failed"))
            if time.monotonic() >= deadline:
                raise RunningHubError(f"RunningHub task timed out: {task_id}")
            await self._sleep_between_polls()

    async def _sleep_between_polls(self) -> None:
        if self.settings.runninghub_poll_interval_seconds > 0:
            await anyio.sleep(self.settings.runninghub_poll_interval_seconds)

    def _extract_output_refs(self, result: dict[str, object]) -> list[dict[str, str | None]]:
        raw_outputs = result.get("data")
        if not isinstance(raw_outputs, list):
            raw_outputs = result.get("results")
        if not isinstance(raw_outputs, list):
            raw_outputs = result.get("outputs")
        if not isinstance(raw_outputs, list):
            return []

        refs: list[dict[str, str | None]] = []
        for item in raw_outputs:
            if not isinstance(item, dict):
                continue
            file_url = item.get("fileUrl") or item.get("file_url") or item.get("url") or item.get("download_url")
            if not file_url:
                continue
            filename = item.get("fileName") or item.get("filename") or str(file_url).split("?")[0].rstrip("/").split("/")[-1]
            refs.append(
                {
                    "file_url": str(file_url),
                    "filename": str(filename or "runninghub-output.png"),
                    "content_type": _content_type_from_output(item, str(filename or "runninghub-output.png")),
                    "node_id": str(item.get("nodeId") or item.get("node_id") or "") or None,
                }
            )
        return refs

    async def _download_outputs(self, refs: list[dict[str, str | None]]) -> list[RunningHubOutput]:
        outputs: list[RunningHubOutput] = []
        async with httpx.AsyncClient(timeout=45) as client:
            for ref in refs:
                file_url = str(ref["file_url"])
                response = await client.get(file_url)
                response.raise_for_status()
                outputs.append(
                    RunningHubOutput(
                        filename=str(ref["filename"]),
                        content_type=str(ref["content_type"]),
                        data=response.content,
                        file_url=file_url,
                        node_id=ref.get("node_id"),
                    )
                )
        return outputs

    async def _post(self, path: str, **kwargs: Any) -> dict[str, object]:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(self._url(path), headers=self._headers(), **kwargs)
            response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RunningHubError("RunningHub returned non-object response")
        return payload

    async def _get(self, path: str, **kwargs: Any) -> dict[str, object]:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.get(self._url(path), headers=self._headers(), **kwargs)
            response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RunningHubError("RunningHub returned non-object response")
        return payload

    def _url(self, path: str) -> str:
        return f"{self.settings.runninghub_base_url.rstrip('/')}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key()}"}

    def _api_key(self) -> str:
        api_key = (self.settings.runninghub_api_key or "").strip()
        if not api_key:
            raise RunningHubError("RUNNINGHUB_API_KEY is required")
        return api_key

    def _require_success(self, payload: dict[str, object]) -> dict[str, object]:
        if int(payload.get("code", 0) or 0) in {0, 200}:
            return payload
        raise RunningHubError(str(payload.get("msg") or payload.get("message") or "RunningHub request failed"))


def _content_type_from_output(item: dict[str, object], filename: str) -> str:
    output_type = str(item.get("fileType") or item.get("outputType") or "").lower()
    if output_type in {"jpg", "jpeg"}:
        return "image/jpeg"
    if output_type == "webp":
        return "image/webp"
    if output_type == "png":
        return "image/png"
    lowered = filename.lower()
    if lowered.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    if lowered.endswith(".webp"):
        return "image/webp"
    return "image/png"


def _image_input_from_demo(payload: dict[str, object]) -> RunningHubWorkflowInput:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        raise RunningHubError("RunningHub AI App demo returned invalid data")
    node_info = data.get("nodeInfoList") or []
    if not isinstance(node_info, list):
        raise RunningHubError("RunningHub AI App demo did not include nodeInfoList")
    for node in node_info:
        if not isinstance(node, dict):
            continue
        field_type = str(node.get("fieldType") or "").upper()
        field_name = str(node.get("fieldName") or "").lower()
        if field_type == "IMAGE" or field_name == "image":
            return RunningHubWorkflowInput.from_dict(node)
    raise RunningHubError("RunningHub AI App demo did not include an image input")


def _looks_pending(message: str) -> bool:
    lowered = message.lower()
    return any(token in lowered for token in ("running", "queue", "pending", "processing", "排队", "运行", "处理中"))


def _looks_failed(payload: dict[str, object]) -> bool:
    text = f"{payload.get('status') or ''} {payload.get('msg') or ''} {payload.get('message') or ''}".lower()
    return any(token in text for token in ("failed", "fail", "error", "失败", "异常"))
