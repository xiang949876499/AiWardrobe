from pathlib import Path

import anyio
import pytest

import app.comfyui as comfyui_module
from app.comfyui import ComfyUIClient
from app.config import Settings


class FakeResponse:
    def __init__(self, payload=None, content: bytes = b"") -> None:
        self.payload = payload or {}
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class FakeAsyncClient:
    calls: list[dict[str, object]] = []
    responses: list[FakeResponse] = []

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"method": "POST", "url": url, **kwargs})
        return self.responses.pop(0)

    async def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append({"method": "GET", "url": url, **kwargs})
        return self.responses.pop(0)


def test_comfyui_uploads_image_rewrites_prompt_and_downloads_outputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "garment_recognition.json"
    workflow_path.write_text(
        """
        {
          "78": {"inputs": {"image": "old.jpg"}, "class_type": "LoadImage"},
          "139": {"inputs": {"filename_prefix": "clothes_top_"}, "class_type": "SaveImage"},
          "151": {"inputs": {"filename_prefix": "clothes_pants_"}, "class_type": "SaveImage"},
          "156": {"inputs": {"filename_prefix": "clothes_shoes_"}, "class_type": "SaveImage"}
        }
        """,
        encoding="utf-8",
    )
    FakeAsyncClient.calls.clear()
    FakeAsyncClient.responses = [
        FakeResponse({"name": "uploaded.jpg", "subfolder": "", "type": "input"}),
        FakeResponse({"prompt_id": "prompt-1"}),
        FakeResponse(
            {
                "prompt-1": {
                    "outputs": {
                        "139": {"images": [{"filename": "top.png", "subfolder": "", "type": "output"}]},
                    }
                }
            }
        ),
        FakeResponse(
            {
                "prompt-1": {
                    "outputs": {
                        "139": {"images": [{"filename": "top.png", "subfolder": "", "type": "output"}]},
                        "151": {"images": [{"filename": "pants.png", "subfolder": "", "type": "output"}]},
                        "156": {"images": [{"filename": "shoes.png", "subfolder": "", "type": "output"}]},
                    }
                }
            }
        ),
        FakeResponse(content=b"top-bytes"),
        FakeResponse(content=b"pants-bytes"),
        FakeResponse(content=b"shoes-bytes"),
    ]
    monkeypatch.setattr(comfyui_module.httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(
        comfyui_base_url="http://127.0.0.1:8188",
        comfyui_garment_workflow_file=str(workflow_path),
        comfyui_poll_interval_seconds=0,
    )

    async def run_workflow():
        return await ComfyUIClient(settings).run_garment_recognition("look.jpg", "image/jpeg", b"image")

    outputs = anyio.run(run_workflow, backend="asyncio")

    prompt_call = FakeAsyncClient.calls[1]
    prompt = prompt_call["json"]["prompt"]
    assert FakeAsyncClient.calls[0]["url"] == "http://127.0.0.1:8188/upload/image"
    assert prompt_call["url"] == "http://127.0.0.1:8188/prompt"
    assert prompt["78"]["inputs"]["image"] == "uploaded.jpg"
    assert outputs[0].category == "top"
    assert outputs[0].filename == "top.png"
    assert outputs[0].data == b"top-bytes"
    assert outputs[1].category == "bottom"
    assert outputs[1].filename == "pants.png"
    assert outputs[1].data == b"pants-bytes"
    assert outputs[2].category == "shoes"
    assert outputs[2].filename == "shoes.png"
    assert outputs[2].data == b"shoes-bytes"
