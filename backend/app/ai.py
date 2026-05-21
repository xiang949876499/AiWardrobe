import base64
import json
from dataclasses import dataclass

import httpx

from app.config import Settings
from app.models import Garment


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
    crop_box: dict[str, int]


class AiService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def analyze_garment(self, filename: str, content_type: str, image_bytes: bytes) -> AiAnalysis:
        if self.settings.ai_demo_mode or not self.settings.ai_api_key:
            return self._demo_analysis(filename)
        try:
            return await self._remote_analysis(content_type, image_bytes)
        except Exception:
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
        return [DetectedGarment(category=base_category, crop_box={"x": 0, "y": 0, "width": 1000, "height": 1000})]

    async def generate_outfit(
        self,
        garments: list[Garment],
        occasion: str,
        season: str,
        temperature: int | None,
        weather: dict[str, object] | None = None,
    ) -> tuple[list[dict[str, object]], str]:
        if self.settings.ai_demo_mode or not self.settings.ai_api_key:
            return self._demo_outfit(garments, occasion, season, temperature, weather)
        try:
            return await self._remote_outfit(garments, occasion, season, temperature)
        except Exception:
            return self._demo_outfit(garments, occasion, season, temperature, weather)

    def _demo_analysis(self, filename: str) -> AiAnalysis:
        name = filename.lower()
        category = "top"
        if any(word in name for word in ["trouser", "pants", "skirt", "jeans"]):
            category = "bottom"
        elif any(word in name for word in ["coat", "jacket", "outer"]):
            category = "outerwear"
        elif any(word in name for word in ["shoe", "sneaker", "loafer", "boot"]):
            category = "shoes"
        elif any(word in name for word in ["bag", "scarf", "hat", "belt"]):
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
            "Analyze this clothing image. Return JSON with keys: category, colors, style, "
            "material, season, fit, tags, confidence. Category must be one of top, bottom, "
            "outerwear, shoes, accessory."
        )
        payload = {
            "model": self.settings.ai_model,
            "response_format": {"type": "json_object"},
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
        }
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{self.settings.ai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.ai_api_key}"},
                json=payload,
            )
            response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        return AiAnalysis(
            category=str(parsed.get("category", "top")),
            colors=list(parsed.get("colors") or ["neutral"]),
            style=str(parsed.get("style", "")),
            material=str(parsed.get("material", "")),
            season=list(parsed.get("season") or []),
            fit=str(parsed.get("fit", "")),
            tags=list(parsed.get("tags") or []),
            confidence=float(parsed.get("confidence", 0.7)),
            raw=parsed,
        )

    def _demo_outfit(
        self,
        garments: list[Garment],
        occasion: str,
        season: str,
        temperature: int | None,
        weather: dict[str, object] | None = None,
    ) -> tuple[list[dict[str, object]], str]:
        selected: list[Garment] = []
        for category in ["top", "bottom", "shoes", "outerwear", "accessory"]:
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
        explanation = f"这套搭配适合{_occasion_label(occasion)}。整体以利落基础款为主{weather_text}，兼顾舒适度和完整度。"
        return items, explanation

    async def _remote_outfit(
        self,
        garments: list[Garment],
        occasion: str,
        season: str,
        temperature: int | None,
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
        payload = {
            "model": self.settings.ai_model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Generate one full outfit from this wardrobe. Return JSON with "
                        "items [{garment_id, category, image_url, reason}] and explanation. "
                        f"Occasion: {occasion}; season: {season}; temperature: {temperature}; "
                        f"wardrobe: {json.dumps(wardrobe, ensure_ascii=False)}"
                    ),
                }
            ],
        }
        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.post(
                f"{self.settings.ai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.settings.ai_api_key}"},
                json=payload,
            )
            response.raise_for_status()
        parsed = json.loads(response.json()["choices"][0]["message"]["content"])
        return list(parsed.get("items") or []), str(parsed.get("explanation", ""))


def _occasion_label(occasion: str) -> str:
    return {
        "work": "上班",
        "date": "约会",
        "sport": "运动",
        "formal": "正式场合",
        "casual": "休闲",
    }.get(occasion, occasion)
