import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anyio
import httpx

from app.config import Settings


class RunningHubError(RuntimeError):
    pass


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
        return cls(node_id=node_id, field_name=field_name)


@dataclass(frozen=True)
class RunningHubWorkflow:
    name: str
    workflow_id: str
    inputs: dict[str, RunningHubWorkflowInput]
    access_password: str | None = None
    task_options: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str | Path) -> "RunningHubWorkflow":
        with Path(path).open("r", encoding="utf-8") as file:
            return cls.from_dict(json.load(file))

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "RunningHubWorkflow":
        workflow_id = str(payload.get("workflow_id") or payload.get("workflowId") or "").strip()
        if not workflow_id:
            raise ValueError("workflow_id is required")
        inputs_payload = payload.get("inputs") or {}
        if not isinstance(inputs_payload, dict) or not inputs_payload:
            raise ValueError("workflow inputs are required")
        task_options = payload.get("task_options") or payload.get("taskOptions") or {}
        if not isinstance(task_options, dict):
            raise ValueError("task_options must be an object")
        access_password = payload.get("access_password") or payload.get("accessPassword")
        return cls(
            name=str(payload.get("name") or "runninghub_workflow"),
            workflow_id=workflow_id,
            inputs={
                str(input_name): RunningHubWorkflowInput.from_dict(input_payload)
                for input_name, input_payload in inputs_payload.items()
                if isinstance(input_payload, dict)
            },
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

    async def query_task(self, task_id: str) -> dict[str, object]:
        payload = await self._post("/openapi/v2/query", json={"taskId": task_id})
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

    async def _post(self, path: str, **kwargs: Any) -> dict[str, object]:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(self._url(path), headers=self._headers(), **kwargs)
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
