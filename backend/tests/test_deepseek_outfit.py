import json
from types import SimpleNamespace

import anyio
import pytest

import app.ai as ai_module
from app.ai import AiService
from app.config import Settings


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "items": [
                                    {
                                        "garment_id": "top-1",
                                        "category": "top",
                                        "image_url": "/top.jpg",
                                        "reason": "适合通勤",
                                    }
                                ],
                                "explanation": "参考天气生成的 DeepSeek 搭配。",
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }


class FakeAsyncClient:
    calls: list[dict[str, object]] = []

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None

    async def post(self, url: str, headers: dict[str, str], json: dict[str, object]) -> FakeResponse:
        self.calls.append({"url": url, "headers": headers, "json": json})
        return FakeResponse()


def test_outfit_recommendation_uses_deepseek_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncClient.calls.clear()
    monkeypatch.setattr(ai_module.httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(
        ai_demo_mode=False,
        deepseek_api_key="deepseek-key",
        outfit_ai_provider="deepseek",
    )
    garment = SimpleNamespace(
        id="top-1",
        category="top",
        colors=["white"],
        style="通勤",
        material="cotton",
        season=["spring"],
        fit="regular",
        tags=["通勤"],
        image_url="/top.jpg",
    )

    async def run_generate() -> tuple[list[dict[str, object]], str]:
        return await AiService(settings).generate_outfit(
            garments=[garment],
            occasion="work",
            season="spring",
            temperature=22,
            weather={"condition": "Cloudy", "temperature": 22, "city": "Shanghai"},
        )

    items, explanation = anyio.run(run_generate, backend="asyncio")

    call = FakeAsyncClient.calls[0]
    payload = call["json"]
    assert call["url"] == "https://api.deepseek.com/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer deepseek-key"
    assert payload["model"] == "deepseek-chat"
    assert "Cloudy" in payload["messages"][0]["content"]
    assert items[0]["garment_id"] == "top-1"
    assert explanation == "参考天气生成的 DeepSeek 搭配。"
