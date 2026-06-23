import json
import logging
from dataclasses import dataclass

import httpx

from app.ai import AiAnalysis
from app.config import Settings
from app.models import Garment

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SimilarityResult:
    garment_id: str
    image_url: str
    similarity: float
    matched_reasons: list[str]


@dataclass(frozen=True)
class PurchaseDecision:
    similar_items: list[SimilarityResult]
    recommendation: str
    score: int
    reason_summary: str
    analysis: dict[str, object]


def score_similarity(candidate: AiAnalysis, garment: Garment) -> SimilarityResult:
    score = 0
    reasons: list[str] = []
    if candidate.category == garment.category:
        score += 30
        reasons.append("same category")
    if _overlaps(candidate.colors, garment.colors):
        score += 20
        reasons.append("similar color")
    if _same_text(candidate.style, garment.style):
        score += 15
        reasons.append("similar style")
    if _same_text(candidate.material, garment.material):
        score += 10
        reasons.append("similar material")
    if _overlaps(candidate.season, garment.season):
        score += 10
        reasons.append("season overlap")
    if _same_text(candidate.fit, garment.fit):
        score += 5
        reasons.append("similar fit")
    if _overlaps(candidate.tags, garment.tags):
        score += 10
        reasons.append("shared tags")
    return SimilarityResult(
        garment_id=garment.id,
        image_url=garment.thumbnail_url or garment.image_url,
        similarity=float(score),
        matched_reasons=reasons,
    )


def analyze_purchase(
    candidate: AiAnalysis,
    garments: list[Garment],
    preferences: dict[str, object] | None = None,
) -> PurchaseDecision:
    ready = [garment for garment in garments if garment.status == "ready"]
    similar_items = sorted(
        (score_similarity(candidate, garment) for garment in ready),
        key=lambda item: item.similarity,
        reverse=True,
    )[:5]
    duplicate_score = int(similar_items[0].similarity) if similar_items else 0
    gap_score = _wardrobe_gap_score(candidate, ready, duplicate_score)
    pairing_score = _pairing_score(candidate, ready)
    score = round((100 - duplicate_score) * 0.3 + gap_score * 0.35 + pairing_score * 0.35)

    if score >= 75 and duplicate_score < 80 and (gap_score >= 55 or pairing_score >= 70):
        recommendation = "recommend"
    elif score < 50 or (duplicate_score >= 80 and gap_score < 65):
        recommendation = "skip"
    else:
        recommendation = "consider"

    decision_factors = _decision_factors(candidate, duplicate_score, gap_score, pairing_score)
    if preferences:
        decision_factors.append(f"user_preferences:{json.dumps(preferences, ensure_ascii=False)}")
    reason_summary = _reason_summary(recommendation, duplicate_score, gap_score, pairing_score)
    return PurchaseDecision(
        similar_items=similar_items,
        recommendation=recommendation,
        score=score,
        reason_summary=reason_summary,
        analysis=_structured_analysis(
            candidate=candidate,
            recommendation=recommendation,
            score=score,
            duplicate_score=duplicate_score,
            gap_score=gap_score,
            pairing_score=pairing_score,
            decision_factors=decision_factors,
            similar_items=similar_items,
        ),
    )


async def explain_purchase(settings: Settings, decision: PurchaseDecision, candidate: AiAnalysis) -> str:
    if settings.ai_demo_mode or not _outfit_api_key(settings):
        return decision.reason_summary
    facts = {
        "candidate": {
            "category": candidate.category,
            "colors": candidate.colors,
            "style": candidate.style,
            "material": candidate.material,
            "season": candidate.season,
            "fit": candidate.fit,
            "tags": candidate.tags,
        },
        "recommendation": decision.recommendation,
        "score": decision.score,
        "analysis": decision.analysis,
        "similar_items": [item.__dict__ for item in decision.similar_items],
    }
    payload = {
        "model": _outfit_model(settings),
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "user",
                "content": (
                    "Write a concise Chinese shopping recommendation explanation from these computed facts. "
                    "Do not change the recommendation. Return JSON: {\"reason_summary\":\"...\"}. "
                    f"Facts: {json.dumps(facts, ensure_ascii=False)}"
                ),
            }
        ],
        "temperature": 0.2,
    }
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{_outfit_base_url(settings).rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {_outfit_api_key(settings)}"},
                json=payload,
            )
            response.raise_for_status()
        parsed = json.loads(response.json()["choices"][0]["message"]["content"])
        summary = str(parsed.get("reason_summary", "")).strip()
        return summary or decision.reason_summary
    except Exception:
        logger.exception("Purchase explanation failed; falling back to deterministic summary")
        return decision.reason_summary


def _wardrobe_gap_score(candidate: AiAnalysis, garments: list[Garment], duplicate_score: int) -> int:
    same_category = [garment for garment in garments if garment.category == candidate.category]
    if not same_category:
        score = 75
    elif len(same_category) == 1:
        score = 55
    elif len(same_category) <= 3:
        score = 40
    else:
        score = 25
    if candidate.colors and not any(_overlaps(candidate.colors, garment.colors) for garment in same_category):
        score += 10
    if candidate.style and not any(_same_text(candidate.style, garment.style) for garment in same_category):
        score += 10
    if candidate.season and not any(_overlaps(candidate.season, garment.season) for garment in same_category):
        score += 5
    if duplicate_score >= 80:
        score -= 20
    elif duplicate_score >= 60:
        score -= 10
    return max(0, min(100, score))


def _pairing_score(candidate: AiAnalysis, garments: list[Garment]) -> int:
    category_counts = {category: 0 for category in ["top", "bottom", "outerwear", "shoes", "bag", "accessory"]}
    for garment in garments:
        if garment.category in category_counts:
            category_counts[garment.category] += 1
    if candidate.category == "top":
        raw = category_counts["bottom"] * 24 + category_counts["shoes"] * 18 + category_counts["outerwear"] * 10
    elif candidate.category == "bottom":
        raw = category_counts["top"] * 24 + category_counts["shoes"] * 18 + category_counts["outerwear"] * 8
    elif candidate.category == "outerwear":
        raw = category_counts["top"] * 18 + category_counts["bottom"] * 14 + category_counts["shoes"] * 10
    elif candidate.category == "shoes":
        raw = category_counts["top"] * 16 + category_counts["bottom"] * 16 + category_counts["outerwear"] * 8
    else:
        raw = len(garments) * 12
    return max(0, min(100, raw))


def _decision_factors(candidate: AiAnalysis, duplicate_score: int, gap_score: int, pairing_score: int) -> list[str]:
    factors = [f"{candidate.category} candidate"]
    if duplicate_score >= 80:
        factors.append("very similar item already owned")
    elif duplicate_score >= 60:
        factors.append("somewhat similar item already owned")
    else:
        factors.append("distinct from current wardrobe")
    if gap_score >= 65:
        factors.append("fills a wardrobe gap")
    if pairing_score >= 70:
        factors.append("strong pairing potential")
    elif pairing_score < 35:
        factors.append("limited pairing support")
    return factors


def _structured_analysis(
    candidate: AiAnalysis,
    recommendation: str,
    score: int,
    duplicate_score: int,
    gap_score: int,
    pairing_score: int,
    decision_factors: list[str],
    similar_items: list[SimilarityResult],
) -> dict[str, object]:
    idle_risk = _idle_risk(duplicate_score, gap_score, pairing_score)
    scene_match = _scene_match_score(candidate)
    match_scenes = _match_scenes(candidate)
    pros, cons = _pros_cons(recommendation, duplicate_score, gap_score, pairing_score)
    summary = _reason_summary(recommendation, duplicate_score, gap_score, pairing_score)
    return {
        "conclusion": recommendation,
        "score": score,
        "summary": summary,
        "dimensions": {
            "outfit_potential": pairing_score,
            "scene_match": scene_match,
            "gap_fill": gap_score,
            "duplicate_risk": duplicate_score,
            "idle_risk": idle_risk,
        },
        "duplicate_risk": duplicate_score,
        "idle_risk": idle_risk,
        "outfit_potential": pairing_score,
        "match_scenes": match_scenes,
        "suggested_price": _suggested_price(candidate, score),
        "score_breakdown": {
            "duplicate_risk": duplicate_score,
            "wardrobe_gap": gap_score,
            "outfit_potential": pairing_score,
            "scene_match": scene_match,
            "idle_risk": idle_risk,
        },
        "pros": pros,
        "cons": cons,
        "similar_items": [item.__dict__ for item in similar_items],
        "outfit_ideas": _outfit_ideas(candidate, match_scenes),
        "idle_risk_detail": _idle_risk_detail(idle_risk),
        "next_actions": ["save", "share", "analyze_another", "upload_wardrobe"],
        "duplicate_score": duplicate_score,
        "wardrobe_gap_score": gap_score,
        "pairing_score": pairing_score,
        "decision_factors": decision_factors,
    }


def _idle_risk(duplicate_score: int, gap_score: int, pairing_score: int) -> int:
    risk = round(duplicate_score * 0.45 + (100 - gap_score) * 0.3 + (100 - pairing_score) * 0.25)
    return max(0, min(100, risk))


def _scene_match_score(candidate: AiAnalysis) -> int:
    tags = " ".join([candidate.style, *candidate.tags]).lower()
    if any(word in tags for word in ["work", "commute", "通勤", "office"]):
        return 82
    if any(word in tags for word in ["sport", "运动", "outdoor"]):
        return 76
    if any(word in tags for word in ["date", "约会", "elegant"]):
        return 78
    return 68


def _match_scenes(candidate: AiAnalysis) -> list[str]:
    tags = " ".join([candidate.style, *candidate.tags]).lower()
    scenes = ["日常"]
    if any(word in tags for word in ["work", "commute", "通勤", "office"]):
        scenes.append("通勤")
    if any(word in tags for word in ["date", "约会", "elegant"]):
        scenes.append("约会")
    if any(word in tags for word in ["sport", "运动", "outdoor"]):
        scenes.append("运动")
    if candidate.category in {"bag", "accessory", "shoes"}:
        scenes.append("搭配补充")
    return scenes[:3]


def _suggested_price(candidate: AiAnalysis, score: int) -> dict[str, int]:
    base = 180 if candidate.category in {"outerwear", "shoes", "bag"} else 120
    ideal = round(base * (0.75 + score / 200))
    return {"min": max(49, ideal - 70), "ideal": max(1, ideal), "max": ideal + 100}


def _pros_cons(
    recommendation: str,
    duplicate_score: int,
    gap_score: int,
    pairing_score: int,
) -> tuple[list[str], list[str]]:
    pros: list[str] = []
    cons: list[str] = []
    if gap_score >= 65:
        pros.append("补足衣橱缺口")
    else:
        cons.append("新增覆盖有限")
    if pairing_score >= 70:
        pros.append("搭配潜力高")
    else:
        cons.append("可搭配方案偏少")
    if duplicate_score >= 70:
        cons.append("已有相似单品")
    else:
        pros.append("与现有衣橱区分明显")
    if recommendation == "skip":
        cons.append("建议先跳过")
    return pros or ["可继续观察"], cons or ["暂无明显风险"]


def _outfit_ideas(candidate: AiAnalysis, scenes: list[str]) -> list[dict[str, object]]:
    return [
        {
            "scene": scene,
            "items": [
                {
                    "category": candidate.category,
                    "image_url": "",
                    "reason": "围绕这件单品建立搭配",
                }
            ],
            "reason": f"适合{scene}场景，建议优先使用衣橱已有基础款组合。",
        }
        for scene in scenes
    ]


def _idle_risk_detail(idle_risk: int) -> dict[str, str]:
    if idle_risk >= 70:
        return {"level": "高", "reason": "重复或不好搭的概率偏高，建议冷静后再买。"}
    if idle_risk >= 45:
        return {"level": "中", "reason": "有一定使用场景，但需要确认价格和搭配。"}
    return {"level": "低", "reason": "与衣橱互补度较好，闲置风险可控。"}


def _reason_summary(recommendation: str, duplicate_score: int, gap_score: int, pairing_score: int) -> str:
    duplicate = "near duplicate risk is high" if duplicate_score >= 80 else "duplicate risk is manageable"
    gap = "fills a clear wardrobe gap" if gap_score >= 65 else "adds limited new coverage"
    pairing = "pairs well with existing items" if pairing_score >= 70 else "has limited pairing options"
    if recommendation == "recommend":
        return f"Recommended: {gap} and {pairing}; {duplicate}."
    if recommendation == "skip":
        return f"Skip for now: {duplicate}, and it {gap.lower()} with {pairing.lower()}."
    return f"Consider: {gap} and {pairing}, while {duplicate}."


def _same_text(left: str | None, right: str | None) -> bool:
    return bool(left and right and left.strip().lower() == right.strip().lower())


def _overlaps(left: list[str] | None, right: list[str] | None) -> bool:
    left_values = {item.strip().lower() for item in left or [] if item.strip()}
    right_values = {item.strip().lower() for item in right or [] if item.strip()}
    return bool(left_values & right_values)


def _outfit_base_url(settings: Settings) -> str:
    if settings.outfit_ai_base_url:
        return settings.outfit_ai_base_url
    if settings.outfit_ai_provider == "deepseek":
        return settings.deepseek_base_url
    return settings.ai_base_url


def _outfit_api_key(settings: Settings) -> str | None:
    if settings.outfit_ai_api_key:
        return settings.outfit_ai_api_key
    if settings.outfit_ai_provider == "deepseek":
        return settings.deepseek_api_key or settings.ai_api_key
    return settings.ai_api_key


def _outfit_model(settings: Settings) -> str:
    if settings.outfit_ai_model:
        return settings.outfit_ai_model
    if settings.outfit_ai_provider == "deepseek":
        return settings.deepseek_model
    return settings.ai_model
