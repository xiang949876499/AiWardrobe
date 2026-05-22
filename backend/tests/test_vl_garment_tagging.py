import json

import anyio
import pytest

import app.ai as ai_module
from app.ai import AiService
from app.config import Settings


class FakeResponse:
    content: dict[str, object] = {
        "category": "上衣",
        "sub_category": "T恤",
        "main_color": "黑色",
        "sleeve_length": "短袖",
        "pant_length": "未知",
        "pattern": "印花",
        "version": "修身",
        "collar_type": "高领",
        "material": "针织",
        "style": "甜美",
        "season": "秋",
        "confidence": 0.98,
    }

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(self.content, ensure_ascii=False)
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


def test_vl_garment_tagging_uses_strict_chinese_json_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncClient.calls.clear()
    monkeypatch.setattr(ai_module.httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(ai_demo_mode=False, ai_api_key="vl-key", ai_base_url="https://vl.example.com", ai_model="vl-test")

    async def run_analysis():
        return await AiService(settings).analyze_garment("shirt.jpg", "image/jpeg", b"image bytes")

    analysis = anyio.run(run_analysis, backend="asyncio")

    call = FakeAsyncClient.calls[0]
    payload = call["json"]
    prompt = payload["messages"][0]["content"][0]["text"]
    assert call["url"] == "https://vl.example.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer vl-key"
    assert payload["model"] == "vl-test"
    assert payload["response_format"] == {"type": "json_object"}
    assert "请识别这张图片中的服装" in prompt
    assert '"category": "品类（只能是：上衣/下装/外套/连衣裙/鞋子/配饰）"' in prompt
    assert "只输出JSON，不要添加任何解释性文字" in prompt
    assert analysis.category == "top"
    assert analysis.colors == ["黑色"]
    assert analysis.style == "甜美"
    assert analysis.material == "针织"
    assert analysis.season == ["秋"]
    assert analysis.fit == "修身"
    assert analysis.confidence == 0.98
    assert analysis.raw["category"] == "上衣"
    assert analysis.raw["sub_category"] == "T恤"
    assert "T恤" in analysis.tags
    assert "印花" in analysis.tags


def test_local_qwen_vl_uses_openai_compatible_v1_endpoint_without_bearer_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeAsyncClient.calls.clear()
    monkeypatch.setattr(ai_module.httpx, "AsyncClient", FakeAsyncClient)
    settings = Settings(
        ai_demo_mode=False,
        ai_api_key=None,
        ai_base_url="http://127.0.0.1:1234",
        ai_model="qwen3-vl-8b-instruct",
    )

    async def run_analysis():
        return await AiService(settings).analyze_garment("shirt.jpg", "image/jpeg", b"image bytes")

    analysis = anyio.run(run_analysis, backend="asyncio")

    call = FakeAsyncClient.calls[0]
    response_format = call["json"]["response_format"]
    assert call["url"] == "http://127.0.0.1:1234/v1/chat/completions"
    assert "Authorization" not in call["headers"]
    assert call["json"]["model"] == "qwen3-vl-8b-instruct"
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"]["required"] == [
        "category",
        "sub_category",
        "main_color",
        "sleeve_length",
        "pant_length",
        "pattern",
        "version",
        "collar_type",
        "material",
        "style",
        "season",
        "confidence",
    ]
    assert call["json"]["temperature"] == 0
    assert call["json"]["max_tokens"] == 512
    assert analysis.raw["category"] == "上衣"


def test_vl_analysis_fills_missing_json_fields_before_saving(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncClient.calls.clear()
    monkeypatch.setattr(ai_module.httpx, "AsyncClient", FakeAsyncClient)
    FakeResponse.content = {
        "category": "上衣",
        "main_color": "黑色",
        "confidence": 0.88,
    }
    settings = Settings(ai_demo_mode=False, ai_api_key="vl-key", ai_base_url="https://vl.example.com/v1", ai_model="vl-test")

    async def run_analysis():
        return await AiService(settings).analyze_garment("shirt.jpg", "image/jpeg", b"image bytes")

    try:
        analysis = anyio.run(run_analysis, backend="asyncio")
    finally:
        FakeResponse.content = {
            "category": "上衣",
            "sub_category": "T恤",
            "main_color": "黑色",
            "sleeve_length": "短袖",
            "pant_length": "未知",
            "pattern": "印花",
            "version": "修身",
            "collar_type": "高领",
            "material": "针织",
            "style": "甜美",
            "season": "秋",
            "confidence": 0.98,
        }

    assert analysis.raw == {
        "category": "上衣",
        "sub_category": "未知",
        "main_color": "黑色",
        "sleeve_length": "未知",
        "pant_length": "未知",
        "pattern": "未知",
        "version": "未知",
        "collar_type": "未知",
        "material": "未知",
        "style": "未知",
        "season": "未知",
        "confidence": 0.88,
    }


def test_vl_analysis_normalizes_unexpected_local_model_values(monkeypatch: pytest.MonkeyPatch) -> None:
    FakeAsyncClient.calls.clear()
    monkeypatch.setattr(ai_module.httpx, "AsyncClient", FakeAsyncClient)
    FakeResponse.content = {
        "category": "女装",
        "sub_category": "T恤",
        "main_color": "浅绿色",
        "sleeve_length": "短袖",
        "pant_length": "中长款",
        "pattern": "纯色",
        "version": "2024",
        "collar_type": "圆领",
        "material": "棉质",
        "style": "休闲",
        "season": "春夏季",
        "confidence": 0.98,
    }
    settings = Settings(ai_demo_mode=False, ai_api_key=None, ai_base_url="http://127.0.0.1:1234", ai_model="qwen3-vl-8b-instruct")

    async def run_analysis():
        return await AiService(settings).analyze_garment("shirt.jpg", "image/jpeg", b"image bytes")

    try:
        analysis = anyio.run(run_analysis, backend="asyncio")
    finally:
        FakeResponse.content = {
            "category": "上衣",
            "sub_category": "T恤",
            "main_color": "黑色",
            "sleeve_length": "短袖",
            "pant_length": "未知",
            "pattern": "印花",
            "version": "修身",
            "collar_type": "高领",
            "material": "针织",
            "style": "甜美",
            "season": "秋",
            "confidence": 0.98,
        }

    assert analysis.category == "top"
    assert analysis.fit == "未知"
    assert analysis.season == ["春"]
    assert analysis.raw["category"] == "上衣"
    assert analysis.raw["version"] == "未知"
    assert analysis.raw["pant_length"] == "未知"
    assert analysis.raw["season"] == "春"
