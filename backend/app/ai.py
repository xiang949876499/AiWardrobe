import base64
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.config import Settings
from app.models import Garment
from app.runninghub import RunningHubClient, RunningHubWorkflow

logger = logging.getLogger(__name__)

VL_REQUIRED_FIELDS = [
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

VL_DEFAULTS: dict[str, object] = {
    "category": "未知",
    "sub_category": "未知",
    "main_color": "未知",
    "sleeve_length": "未知",
    "pant_length": "未知",
    "pattern": "未知",
    "version": "未知",
    "collar_type": "未知",
    "material": "未知",
    "style": "未知",
    "season": "未知",
    "confidence": 0.7,
}

VL_ALLOWED_VALUES = {
    "category": {"上衣", "下装", "外套", "连衣裙", "鞋子", "包", "包袋", "配饰", "饰品", "未知"},
    "sleeve_length": {"长袖", "短袖", "无袖", "七分袖", "未知"},
    "pant_length": {"长裤", "九分裤", "短裤", "短裙", "中长裙", "未知"},
    "pattern": {"纯色", "条纹", "格子", "波点", "印花", "其他", "未知"},
    "version": {"修身", "宽松", "直筒", "紧身", "oversize", "标准", "未知"},
    "collar_type": {"翻领", "圆领", "V领", "立领", "连帽", "其他", "未知"},
    "season": {"春", "夏", "秋", "冬", "四季通用", "未知"},
}

VL_JSON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": VL_REQUIRED_FIELDS,
    "properties": {
        "category": {"type": "string"},
        "sub_category": {"type": "string"},
        "main_color": {"type": "string"},
        "sleeve_length": {"type": "string"},
        "pant_length": {"type": "string"},
        "pattern": {"type": "string"},
        "version": {"type": "string"},
        "collar_type": {"type": "string"},
        "material": {"type": "string"},
        "style": {"type": "string"},
        "season": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
}


@dataclass(frozen=True)
class AiAnalysis:
    category: str
    colors: list[str]
    style: str
    material: str
    season: list[str]
    fit: str
    tags: list[str]
    confidence: float
    raw: dict[str, object]


@dataclass(frozen=True)
class DetectedGarment:
    category: str
    crop_box: dict[str, int] | None


class AiService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def analyze_garment(self, filename: str, content_type: str, image_bytes: bytes) -> AiAnalysis:
        if self.settings.garment_ai_provider == "runninghub" and self.settings.runninghub_api_key:
            try:
                return await self._runninghub_analysis(filename, content_type, image_bytes)
            except Exception:
                logger.exception("RunningHub garment analysis failed; falling back to demo analysis")
                return self._demo_analysis(filename)
        if self.settings.ai_demo_mode or not self._can_use_remote_garment_ai():
            return self._demo_analysis(filename)
        try:
            return await self._remote_analysis(content_type, image_bytes)
        except Exception:
            logger.exception("VL garment analysis failed; falling back to demo analysis")
            return self._demo_analysis(filename)

    async def detect_garments(self, filename: str, content_type: str, image_bytes: bytes) -> list[DetectedGarment]:
        name = filename.lower()
        if "empty" in name or "none" in name:
            return []
        base_category = self._demo_analysis(filename).category
        if "multi" in name or "look" in name:
            return [
                DetectedGarment(category="top", crop_box={"x": 40, "y": 30, "width": 420, "height": 420}),
                DetectedGarment(category="bottom", crop_box={"x": 50, "y": 430, "width": 400, "height": 430}),
            ]
        return [DetectedGarment(category=base_category, crop_box=None)]

    async def generate_outfit(
        self,
        garments: list[Garment],
        occasion: str,
        season: str,
        temperature: int | None,
        weather: dict[str, object] | None = None,
        preferences: dict[str, object] | None = None,
    ) -> tuple[list[dict[str, object]], str]:
        if self.settings.ai_demo_mode or not self._outfit_api_key():
            return self._demo_outfit(garments, occasion, season, temperature, weather, preferences)
        try:
            return await self._remote_outfit(garments, occasion, season, temperature, weather, preferences)
        except Exception:
            return self._demo_outfit(garments, occasion, season, temperature, weather, preferences)

    def _demo_analysis(self, filename: str) -> AiAnalysis:
        name = filename.lower()
        category = "top"
        if any(word in name for word in ["trouser", "pants", "skirt", "jeans"]):
            category = "bottom"
        elif any(word in name for word in ["coat", "jacket", "outer"]):
            category = "outerwear"
        elif any(word in name for word in ["shoe", "sneaker", "loafer", "boot"]):
            category = "shoes"
        elif any(word in name for word in ["bag", "tote", "purse"]):
            category = "bag"
        elif any(word in name for word in ["scarf", "hat", "belt", "jewelry", "necklace"]):
            category = "accessory"

        color = "white"
        if any(word in name for word in ["black", "dark"]):
            color = "black"
        elif any(word in name for word in ["blue", "denim"]):
            color = "blue"
        elif any(word in name for word in ["pink", "rose"]):
            color = "pink"

        raw = {
            "category": category,
            "colors": [color],
            "style": "通勤休闲",
            "material": "混纺",
            "season": ["spring", "autumn"],
            "fit": "regular",
            "tags": ["通勤", "休闲"],
            "source": "demo",
        }
        return AiAnalysis(
            category=category,
            colors=[color],
            style="通勤休闲",
            material="混纺",
            season=["spring", "autumn"],
            fit="regular",
            tags=["通勤", "休闲"],
            confidence=0.82,
            raw=raw,
        )

    async def _remote_analysis(self, content_type: str, image_bytes: bytes) -> AiAnalysis:
        image_base64 = base64.b64encode(image_bytes).decode("ascii")
        prompt = (
            "请识别这张图片中的服装，严格按照以下JSON格式输出，不要添加任何其他文字：\n"
            "{\n"
            "\"category\": \"品类（只能是：上衣/下装/外套/连衣裙/鞋子/配饰）\",\n"
            "\"sub_category\": \"子品类（如：T恤/衬衫/牛仔裤/西装/运动鞋等）\",\n"
            "\"main_color\": \"主颜色（如：白色/黑色/蓝色/红色等）\",\n"
            "\"sleeve_length\": \"袖长（上衣必填，只能是：长袖/短袖/无袖/七分袖）\",\n"
            "\"pant_length\": \"裤长（下装必填，只能是：长裤/九分裤/短裤/短裙/中长裙）\",\n"
            "\"pattern\": \"图案（只能是：纯色/条纹/格子/波点/印花/其他）\",\n"
            "\"version\": \"版型（只能是：修身/宽松/直筒/紧身/oversize/标准）\",\n"
            "\"collar_type\": \"领型（上衣必填，只能是：翻领/圆领/V领/立领/连帽/其他）\",\n"
            "\"material\": \"材质（如：纯棉/牛仔/丝绸/羊毛/皮革/涤纶/针织等）\",\n"
            "\"style\": \"风格（如：简约/通勤/休闲/运动/复古/甜美/正式/街头等）\",\n"
            "\"season\": \"适用季节（只能是：春/夏/秋/冬/四季通用）\",\n"
            "\"confidence\": 识别置信度（0-1之间的数字）\n"
            "}\n\n"
            "注意：\n"
            "如果某个属性无法确定，填\"未知\"\n"
            "严格遵守JSON格式，不要有语法错误\n"
            "只输出JSON，不要添加任何解释性文字"
        )
        payload = {
            "model": self.settings.ai_model,
            "response_format": self._garment_response_format(),
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{content_type};base64,{image_base64}"},
                        },
                    ],
                }
            ],
            "temperature": 0,
            "max_tokens": 512,
        }
        async with httpx.AsyncClient(timeout=45) as client:
            headers = {}
            if self.settings.ai_api_key:
                headers["Authorization"] = f"Bearer {self.settings.ai_api_key}"
            response = await client.post(
                self._chat_completions_url(self.settings.ai_base_url),
                headers=headers,
                json=payload,
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = _complete_vl_payload(json.loads(content))
        logger.info("VL garment analysis returned fields: %s", sorted(parsed.keys()))
        category = _normalize_vl_category(str(parsed.get("category", "上衣")))
        sub_category = _string_value(parsed, "sub_category")
        pattern = _string_value(parsed, "pattern")
        sleeve_length = _string_value(parsed, "sleeve_length")
        pant_length = _string_value(parsed, "pant_length")
        collar_type = _string_value(parsed, "collar_type")
        style = _string_value(parsed, "style")
        return AiAnalysis(
            category=category,
            colors=_compact_unknowns([_string_value(parsed, "main_color")]) or ["未知"],
            style=style,
            material=_string_value(parsed, "material"),
            season=_compact_unknowns([_string_value(parsed, "season")]),
            fit=_string_value(parsed, "version"),
            tags=_compact_unknowns([sub_category, pattern, sleeve_length, pant_length, collar_type, style]),
            confidence=float(parsed.get("confidence", 0.7)),
            raw=parsed,
        )

    async def _runninghub_analysis(self, filename: str, content_type: str, image_bytes: bytes) -> AiAnalysis:
        workflow = RunningHubWorkflow.from_file(self._runninghub_garment_workflow_path())
        inputs: dict[str, object] = {}
        for prompt_input in ["prompt", "text"]:
            if prompt_input in workflow.inputs:
                inputs[prompt_input] = _garment_tagging_prompt()
        result = await RunningHubClient(self.settings).run_workflow(
            workflow,
            inputs=inputs,
            files={"image": (filename, content_type, image_bytes)},
        )
        parsed = _complete_vl_payload(json.loads(_extract_runninghub_text_result(result)))
        logger.info("RunningHub garment analysis returned fields: %s", sorted(parsed.keys()))
        return _analysis_from_vl_payload(parsed)

    def _runninghub_garment_workflow_path(self) -> Path:
        workflow_path = Path(self.settings.runninghub_garment_workflow_file)
        if workflow_path.is_absolute():
            return workflow_path
        return Path(__file__).resolve().parents[1] / workflow_path

    def _demo_outfit(
        self,
        garments: list[Garment],
        occasion: str,
        season: str,
        temperature: int | None,
        weather: dict[str, object] | None = None,
        preferences: dict[str, object] | None = None,
    ) -> tuple[list[dict[str, object]], str]:
        selected: list[Garment] = []
        for category in ["top", "bottom", "shoes", "outerwear", "bag", "accessory"]:
            match = next((garment for garment in garments if garment.category == category), None)
            if match:
                selected.append(match)
        if len(selected) < 3:
            selected = garments[:3]

        items = [
            {
                "garment_id": garment.id,
                "category": garment.category,
                "image_url": garment.image_url,
                "reason": f"{garment.style or '基础'}单品适合{_occasion_label(occasion)}场景",
            }
            for garment in selected
        ]
        weather_text = ""
        if weather:
            weather_text = f"，参考天气：{weather.get('condition', '未知')}，{weather.get('temperature', temperature or '--')}度"
        else:
            weather_text = f"，适配{temperature}度" if temperature is not None else f"，适配{season or '当前天气'}"
        preference_parts: list[str] = []
        if preferences:
            if preferences.get("primary_goal"):
                preference_parts.append(f"目标：{preferences['primary_goal']}")
            if preferences.get("scenes"):
                preference_parts.append(f"场景：{'/'.join(preferences['scenes'][:3])}")
            if preferences.get("styles"):
                preference_parts.append(f"风格：{'/'.join(preferences['styles'][:3])}")
        preference_text = f"，参考你的偏好：{'；'.join(preference_parts)}" if preference_parts else ""
        explanation = f"这套搭配适合{_occasion_label(occasion)}。整体以利落基础款为主{weather_text}{preference_text}，兼顾舒适度和完整度。"
        return items, explanation

    async def _remote_outfit(
        self,
        garments: list[Garment],
        occasion: str,
        season: str,
        temperature: int | None,
        weather: dict[str, object] | None = None,
        preferences: dict[str, object] | None = None,
    ) -> tuple[list[dict[str, object]], str]:
        wardrobe = [
            {
                "id": garment.id,
                "category": garment.category,
                "colors": garment.colors,
                "style": garment.style,
                "material": garment.material,
                "season": garment.season,
                "fit": garment.fit,
                "tags": garment.tags,
                "image_url": garment.image_url,
            }
            for garment in garments
        ]
        weather_context = json.dumps(weather or {}, ensure_ascii=False)
        preference_context = json.dumps(preferences or {}, ensure_ascii=False)
        payload = {
            "model": self._outfit_model(),
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Generate one full outfit from this wardrobe. Return JSON with "
                        "items [{garment_id, category, reason}] and explanation. "
                        "Only choose garment_id values from the wardrobe; do not invent or rewrite image URLs. "
                        f"Occasion: {occasion}; season: {season}; temperature: {temperature}; "
                        f"weather: {weather_context}; "
                        f"user_preferences: {preference_context}; "
                        f"wardrobe: {json.dumps(wardrobe, ensure_ascii=False)}"
                    ),
                }
            ],
        }
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{self._outfit_base_url().rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self._outfit_api_key()}"},
                json=payload,
            )
            response.raise_for_status()
        parsed = json.loads(response.json()["choices"][0]["message"]["content"])
        return list(parsed.get("items") or []), str(parsed.get("explanation", ""))

    def _outfit_base_url(self) -> str:
        if self.settings.outfit_ai_base_url:
            return self.settings.outfit_ai_base_url
        if self.settings.outfit_ai_provider == "deepseek":
            return self.settings.deepseek_base_url
        return self.settings.ai_base_url

    def _outfit_api_key(self) -> str | None:
        if self.settings.outfit_ai_api_key:
            return self.settings.outfit_ai_api_key
        if self.settings.outfit_ai_provider == "deepseek":
            return self.settings.deepseek_api_key or self.settings.ai_api_key
        return self.settings.ai_api_key

    def _outfit_model(self) -> str:
        if self.settings.outfit_ai_model:
            return self.settings.outfit_ai_model
        if self.settings.outfit_ai_provider == "deepseek":
            return self.settings.deepseek_model
        return self.settings.ai_model

    def _can_use_remote_garment_ai(self) -> bool:
        if self.settings.ai_api_key:
            return True
        return _is_local_base_url(self.settings.ai_base_url)

    def _chat_completions_url(self, base_url: str) -> str:
        normalized = base_url.rstrip("/")
        if normalized.endswith("/chat/completions"):
            return normalized
        if normalized.endswith("/v1"):
            return f"{normalized}/chat/completions"
        return f"{normalized}/v1/chat/completions"

    def _garment_response_format(self) -> dict[str, object]:
        if _is_local_base_url(self.settings.ai_base_url):
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": "garment_tags",
                    "strict": True,
                    "schema": VL_JSON_SCHEMA,
                },
            }
        return {"type": "json_object"}


def _analysis_from_vl_payload(parsed: dict[str, object]) -> AiAnalysis:
    category = _normalize_vl_category(str(parsed.get("category", "上衣")))
    sub_category = _string_value(parsed, "sub_category")
    pattern = _string_value(parsed, "pattern")
    sleeve_length = _string_value(parsed, "sleeve_length")
    pant_length = _string_value(parsed, "pant_length")
    collar_type = _string_value(parsed, "collar_type")
    style = _string_value(parsed, "style")
    return AiAnalysis(
        category=category,
        colors=_compact_unknowns([_string_value(parsed, "main_color")]) or ["未知"],
        style=style,
        material=_string_value(parsed, "material"),
        season=_compact_unknowns([_string_value(parsed, "season")]),
        fit=_string_value(parsed, "version"),
        tags=_compact_unknowns([sub_category, pattern, sleeve_length, pant_length, collar_type, style]),
        confidence=float(parsed.get("confidence", 0.7)),
        raw=parsed,
    )


def _extract_runninghub_text_result(result: dict[str, object]) -> str:
    candidates = result.get("results") or result.get("data") or result
    text = _find_json_text(candidates)
    if text is None:
        raise ValueError("RunningHub result does not contain JSON text output")
    return text


def _find_json_text(value: object) -> str | None:
    if isinstance(value, str):
        clean = value.strip()
        if clean.startswith("{") or clean.startswith("["):
            return clean
        return None
    if isinstance(value, list):
        for item in value:
            found = _find_json_text(item)
            if found is not None:
                return found
        return None
    if isinstance(value, dict):
        for key in ["text", "content", "value", "json", "output", "result"]:
            if key in value:
                found = _find_json_text(value[key])
                if found is not None:
                    return found
        for item in value.values():
            found = _find_json_text(item)
            if found is not None:
                return found
    return None


def _garment_tagging_prompt() -> str:
    return """请识别这张图片中的服装，严格按照以下JSON格式输出，不要添加任何其他文字：
{
"category": "品类（只能是：上衣/下装/外套/连衣裙/鞋子/配饰）",
"sub_category": "子品类（如：T恤/衬衫/牛仔裤/西装/运动鞋等）",
"main_color": "主颜色（如：白色/黑色/蓝色/红色等）",
"sleeve_length": "袖长（上衣必填，只能是：长袖/短袖/无袖/七分袖）",
"pant_length": "裤长（下装必填，只能是：长裤/九分裤/短裤/短裙/中长裙）",
"pattern": "图案（只能是：纯色/条纹/格子/波点/印花/其他）",
"version": "版型（只能是：修身/宽松/直筒/紧身/oversize/标准）",
"collar_type": "领型（上衣必填，只能是：翻领/圆领/V领/立领/连帽/其他）",
"material": "材质（如：纯棉/牛仔/丝绸/羊毛/皮革/涤纶/针织等）",
"style": "风格（如：简约/通勤/休闲/运动/复古/甜美/正式/街头等）",
"season": "适用季节（只能是：春/夏/秋/冬/四季通用）",
"confidence": 识别置信度（0-1之间的数字）
}

注意：
如果某个属性无法确定，填"未知"
严格遵守JSON格式，不要有语法错误
只输出JSON，不要添加任何解释性文字"""


def _occasion_label(occasion: str) -> str:
    return {
        "work": "上班",
        "date": "约会",
        "sport": "运动",
        "formal": "正式场合",
        "casual": "休闲",
    }.get(occasion, occasion)


def _normalize_vl_category(category: str) -> str:
    return {
        "上衣": "top",
        "下装": "bottom",
        "连衣裙": "bottom",
        "外套": "outerwear",
        "鞋子": "shoes",
        "包": "bag",
        "包袋": "bag",
        "配饰": "accessory",
        "饰品": "accessory",
    }.get(category.strip(), "top")


def _string_value(payload: dict[str, object], key: str) -> str:
    value = payload.get(key, "未知")
    if value is None:
        return "未知"
    return str(value).strip() or "未知"


def _compact_unknowns(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        clean = value.strip()
        if clean and clean != "未知" and clean not in result:
            result.append(clean)
    return result


def _complete_vl_payload(payload: dict[str, object]) -> dict[str, object]:
    missing = [field for field in VL_REQUIRED_FIELDS if field not in payload]
    if missing:
        logger.warning("VL garment analysis missing fields, filling defaults: %s", missing)
    complete = dict(VL_DEFAULTS)
    complete.update(payload)
    complete["category"] = _normalize_raw_category(str(complete["category"]), str(complete["sub_category"]))
    for field, allowed in VL_ALLOWED_VALUES.items():
        value = str(complete[field]).strip()
        if field == "season":
            value = _normalize_raw_season(value)
        if value not in allowed:
            logger.warning("VL garment analysis invalid %s=%r, replacing with 未知", field, value)
            value = "未知"
        complete[field] = value
    try:
        complete["confidence"] = max(0.0, min(1.0, float(complete["confidence"])))
    except (TypeError, ValueError):
        complete["confidence"] = 0.7
    return complete


def _normalize_raw_category(category: str, sub_category: str) -> str:
    clean = category.strip()
    if clean in VL_ALLOWED_VALUES["category"]:
        return clean
    sub = sub_category.strip()
    if any(keyword in sub for keyword in ["T恤", "衬衫", "毛衣", "卫衣", "背心", "针织衫"]):
        return "上衣"
    if any(keyword in sub for keyword in ["裤", "裙", "牛仔裤"]):
        return "下装"
    if any(keyword in sub for keyword in ["外套", "西装", "夹克", "大衣", "风衣"]):
        return "外套"
    if any(keyword in sub for keyword in ["鞋", "靴"]):
        return "鞋子"
    if any(keyword in sub for keyword in ["包", "手袋", "托特", "背包"]):
        return "包"
    if any(keyword in sub for keyword in ["帽", "围巾", "腰带", "首饰", "项链", "耳环"]):
        return "配饰"
    return clean


def _normalize_raw_season(season: str) -> str:
    clean = season.strip()
    if clean in VL_ALLOWED_VALUES["season"]:
        return clean
    for value in ["春", "夏", "秋", "冬"]:
        if value in clean:
            return value
    if "四季" in clean or "全年" in clean:
        return "四季通用"
    return clean


def _is_local_base_url(base_url: str) -> bool:
    hostname = urlparse(base_url).hostname
    return hostname in {"127.0.0.1", "localhost", "::1"}
