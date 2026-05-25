import copy
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
import httpx

from app.config import Settings


class ComfyUIError(RuntimeError):
    pass


@dataclass(frozen=True)
class ComfyUIOutput:
    category: str
    filename: str
    subfolder: str
    type: str
    data: bytes


class ComfyUIClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = settings.comfyui_base_url.rstrip("/")

    async def run_garment_recognition(self, filename: str, content_type: str, image_bytes: bytes) -> list[ComfyUIOutput]:
        async with httpx.AsyncClient(timeout=self.settings.comfyui_poll_timeout_seconds) as client:
            uploaded = await self._upload_image(client, filename, content_type, image_bytes)
            prompt = self._garment_prompt(uploaded["name"])
            prompt_id = await self._queue_prompt(client, prompt)
            history = await self._wait_for_history(client, prompt_id, _save_image_node_ids(prompt))
            return await self._download_outputs(client, prompt, history)

    async def _upload_image(
        self,
        client: httpx.AsyncClient,
        filename: str,
        content_type: str,
        image_bytes: bytes,
    ) -> dict[str, str]:
        response = await client.post(
            f"{self.base_url}/upload/image",
            files={"image": (filename, image_bytes, content_type)},
            data={"overwrite": "true"},
        )
        response.raise_for_status()
        payload = response.json()
        name = str(payload.get("name") or "")
        if not name:
            raise ComfyUIError("ComfyUI upload response did not include an image name")
        return {
            "name": name,
            "subfolder": str(payload.get("subfolder") or ""),
            "type": str(payload.get("type") or "input"),
        }

    def _garment_prompt(self, uploaded_name: str) -> dict[str, Any]:
        workflow = copy.deepcopy(self._load_workflow())
        node = workflow.get(self.settings.comfyui_load_image_node_id) or _find_node_by_class(workflow, "LoadImage")
        if node is None:
            raise ComfyUIError("ComfyUI garment workflow does not contain a LoadImage node")
        node.setdefault("inputs", {})["image"] = uploaded_name
        return workflow

    def _load_workflow(self) -> dict[str, Any]:
        path = Path(self.settings.comfyui_garment_workflow_file)
        if not path.is_absolute():
            path = Path(__file__).resolve().parents[1] / path
        return json.loads(path.read_text(encoding="utf-8"))

    async def _queue_prompt(self, client: httpx.AsyncClient, prompt: dict[str, Any]) -> str:
        response = await client.post(
            f"{self.base_url}/prompt",
            json={"prompt": prompt, "client_id": self.settings.comfyui_client_id},
        )
        response.raise_for_status()
        prompt_id = str(response.json().get("prompt_id") or "")
        if not prompt_id:
            raise ComfyUIError("ComfyUI prompt response did not include prompt_id")
        return prompt_id

    async def _wait_for_history(
        self,
        client: httpx.AsyncClient,
        prompt_id: str,
        expected_output_node_ids: set[str],
    ) -> dict[str, Any]:
        deadline = anyio.current_time() + self.settings.comfyui_poll_timeout_seconds
        while True:
            response = await client.get(f"{self.base_url}/history/{prompt_id}")
            response.raise_for_status()
            history = response.json()
            entry = history.get(prompt_id)
            if entry and _has_expected_outputs(entry, expected_output_node_ids):
                return entry
            if anyio.current_time() >= deadline:
                raise ComfyUIError(f"ComfyUI prompt {prompt_id} timed out")
            await anyio.sleep(self.settings.comfyui_poll_interval_seconds)

    async def _download_outputs(
        self,
        client: httpx.AsyncClient,
        prompt: dict[str, Any],
        history: dict[str, Any],
    ) -> list[ComfyUIOutput]:
        outputs: list[ComfyUIOutput] = []
        for node_id, output in (history.get("outputs") or {}).items():
            for image in output.get("images") or []:
                filename = str(image.get("filename") or "")
                if not filename:
                    continue
                subfolder = str(image.get("subfolder") or "")
                image_type = str(image.get("type") or "output")
                response = await client.get(
                    f"{self.base_url}/view",
                    params={"filename": filename, "subfolder": subfolder, "type": image_type},
                )
                response.raise_for_status()
                outputs.append(
                    ComfyUIOutput(
                        category=_category_for_output(str(node_id), prompt, filename),
                        filename=filename,
                        subfolder=subfolder,
                        type=image_type,
                        data=response.content,
                    )
                )
        return outputs


def _find_node_by_class(workflow: dict[str, Any], class_type: str) -> dict[str, Any] | None:
    for node in workflow.values():
        if isinstance(node, dict) and node.get("class_type") == class_type:
            return node
    return None


def _save_image_node_ids(workflow: dict[str, Any]) -> set[str]:
    return {
        str(node_id)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and node.get("class_type") == "SaveImage"
    }


def _has_expected_outputs(entry: dict[str, Any], expected_output_node_ids: set[str]) -> bool:
    outputs = entry.get("outputs") or {}
    if not outputs:
        return False
    if not expected_output_node_ids:
        return True
    for node_id in expected_output_node_ids:
        images = (outputs.get(node_id) or {}).get("images") or []
        if not images:
            return False
    return True


def _category_for_output(node_id: str, prompt: dict[str, Any], filename: str) -> str:
    node = prompt.get(node_id) or {}
    prefix = str((node.get("inputs") or {}).get("filename_prefix") or "").lower()
    haystack = f"{prefix} {filename.lower()}"
    if any(token in haystack for token in ["pants", "trouser", "bottom", "skirt"]):
        return "bottom"
    if any(token in haystack for token in ["shoe", "sneaker", "boot"]):
        return "shoes"
    if any(token in haystack for token in ["coat", "jacket", "outer"]):
        return "outerwear"
    if "bag" in haystack:
        return "bag"
    if any(token in haystack for token in ["accessory", "scarf", "hat", "belt", "jewelry"]):
        return "accessory"
    return "top"
