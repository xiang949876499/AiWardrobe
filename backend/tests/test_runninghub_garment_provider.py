import json
from pathlib import Path

import anyio
import pytest

import app.ai as ai_module
from app.ai import AiService
from app.config import Settings


class FakeRunningHubClient:
    calls: list[dict[str, object]] = []

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def run_workflow(self, workflow, inputs=None, files=None):
        self.calls.append({"workflow": workflow, "inputs": inputs, "files": files})
        return {
            "status": "SUCCESS",
            "results": [
                {
                    "outputType": "json",
                    "text": json.dumps(
                        {
                            "category": "上衣",
                            "sub_category": "T恤",
                            "main_color": "黑色",
                            "sleeve_length": "短袖",
                            "pant_length": "未知",
                            "pattern": "印花",
                            "version": "修身",
                            "collar_type": "圆领",
                            "material": "纯棉",
                            "style": "休闲",
                            "season": "夏",
                            "confidence": 0.96,
                        },
                        ensure_ascii=False,
                    ),
                }
            ],
        }


def test_garment_analysis_can_use_runninghub_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow_path = tmp_path / "garment_recognition.json"
    workflow_path.write_text(
        json.dumps(
            {
                "name": "garment_recognition",
                "workflow_id": "workflow-123",
                "inputs": {"image": {"node_id": "14", "field_name": "image"}},
            }
        ),
        encoding="utf-8",
    )
    FakeRunningHubClient.calls.clear()
    monkeypatch.setattr(ai_module, "RunningHubClient", FakeRunningHubClient)
    settings = Settings(
        ai_demo_mode=False,
        garment_ai_provider="runninghub",
        runninghub_api_key="rh-key",
        runninghub_garment_workflow_file=str(workflow_path),
    )

    async def run_analysis():
        return await AiService(settings).analyze_garment("shirt.png", "image/png", b"image")

    analysis = anyio.run(run_analysis, backend="asyncio")

    call = FakeRunningHubClient.calls[0]
    assert call["workflow"].workflow_id == "workflow-123"
    assert call["files"] == {"image": ("shirt.png", "image/png", b"image")}
    assert analysis.category == "top"
    assert analysis.colors == ["黑色"]
    assert analysis.style == "休闲"
    assert analysis.material == "纯棉"
    assert analysis.season == ["夏"]
    assert analysis.fit == "修身"
    assert analysis.confidence == 0.96
    assert analysis.raw["category"] == "上衣"
