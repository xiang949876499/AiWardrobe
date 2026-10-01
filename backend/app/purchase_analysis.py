import json
import logging
from dataclasses import dataclass
from itertools import product

import httpx

from app.ai import AiAnalysis
from app.config import Settings
from app.models import Garment

logger = logging.getLogger(__name__)
PURCHASE_RULE_VERSION = "purchase-v2-combinations"


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
    score: int | None
    reason_summary: str
    analysis: dict[str, object]
    coverage: dict[str, object]
    evidence: dict[str, object]


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
    *,
    actual_price: object | None = None,
    currency: str | None = None,
    feedback_adjustment: int = 0,
    candidate_id: str | None = None,
    user_id: str | None = None,
    excluded_garment_ids: set[str] | None = None,
) -> PurchaseDecision:
    excluded_ids = set(excluded_garment_ids or ()) | set((preferences or {}).get("excluded_garment_ids") or ())
    ready = [
        garment for garment in garments
        if garment.status == "ready"
        and garment.review_status == "confirmed"
        and garment.user_confirmed_at is not None
        and (user_id is None or garment.user_id == user_id)
        and garment.id not in excluded_ids
    ]
    similar_items = sorted(
        (score_similarity(candidate, garment) for garment in ready),
        key=lambda item: item.similarity,
        reverse=True,
    )[:5]
    duplicate_score = int(similar_items[0].similarity) if similar_items else 0
    normalized_price = _numeric_price(actual_price)
    budget_max = _numeric_price((preferences or {}).get("budget_max"))
    avoid_categories = {
        _normalize_label(value)
        for value in (preferences or {}).get("avoid_categories", [])
        if isinstance(value, str)
    }
    hard_constraints: list[str] = []
    if _normalize_label(candidate.category) in avoid_categories:
        hard_constraints.append("category_avoided")
    if normalized_price is not None and budget_max is not None and normalized_price > budget_max:
        hard_constraints.append("over_budget")

    budget_match = (
        None
        if normalized_price is None or budget_max is None
        else normalized_price <= budget_max
    )
    eligible = [garment for garment in ready if _normalize_label(garment.category) not in avoid_categories]
    combinations = _outfit_ideas(candidate, eligible, _match_scenes(candidate), candidate_id)
    # Category evidence supports comparisons; complete complementary categories
    # support pairing. A lone unrelated bag cannot establish either dimension.
    same_category = any(garment.category == candidate.category for garment in ready)
    wardrobe_core = {"top", "bottom", "shoes"} <= {garment.category for garment in ready}
    pairing_categories = {candidate.category, *(garment.category for garment in eligible)}
    pairing_covered = (
        bool(candidate_id)
        and candidate.category in _OUTFIT_CATEGORIES
        and _normalize_label(candidate.category) not in avoid_categories
        and {"top", "bottom", "shoes"} <= pairing_categories
    )
    # Unknown seasons may produce a clearly labelled idea, but cannot justify a
    # numeric seasonal pairing claim. Known incompatible seasons justify zero.
    unknown_only = bool(combinations) and all(group["warnings"] for group in combinations)
    pairing_covered = pairing_covered and not unknown_only
    comparison_covered = same_category or wardrobe_core
    dimensions = {
        "duplicate_risk": comparison_covered,
        "gap_fill": comparison_covered,
        "outfit_potential": pairing_covered,
        "idle_risk": comparison_covered and pairing_covered,
        "budget_match": budget_match,
    }
    coverage = {
        "status": "sufficient" if comparison_covered and pairing_covered else "insufficient",
        "confirmed_garment_count": len(ready),
        "dimensions": dimensions,
        "missing": (["confirmed_wardrobe"] if not ready else
                    [name for name, available in dimensions.items() if available is False]),
    }
    evidence = {
        "rule_version": PURCHASE_RULE_VERSION,
        "analysis_source": candidate.analysis_source,
        "provider": candidate.provider,
        "model_version": candidate.model_version,
        "confirmed_garment_ids": [garment.id for garment in ready],
        "actual_price": str(normalized_price) if normalized_price is not None else None,
        "currency": currency if normalized_price is not None else None,
        "budget_max": str(budget_max) if budget_max is not None else None,
        "hard_constraints": hard_constraints,
        "excluded_garment_ids": sorted(excluded_ids),
    }

    if not ready and not hard_constraints:
        analysis = _insufficient_analysis(candidate, evidence, coverage)
        return PurchaseDecision(
            similar_items=[],
            recommendation="consider",
            score=None,
            reason_summary="资料不足：请先确认几件常穿衣物，再查看个人化购买建议。",
            analysis=analysis,
            coverage=coverage,
            evidence=evidence,
        )

    if hard_constraints:
        recommendation = "skip"
        score: int | None = None
        reasons = ["该品类已在你的明确排除项中。" if item == "category_avoided" else "实际价格超过你的预算上限。" for item in hard_constraints]
        reason_summary = " ".join(reasons)
        analysis = _hard_constraint_analysis(candidate, evidence, coverage, reason_summary)
        return PurchaseDecision(
            similar_items=similar_items,
            recommendation=recommendation,
            score=score,
            reason_summary=reason_summary,
            analysis=analysis,
            coverage=coverage,
            evidence=evidence,
        )

    gap_score = _wardrobe_gap_score(candidate, ready, duplicate_score)
    pairing_score = min(100, sum(not group["warnings"] for group in combinations) * 30)
    if coverage["status"] == "insufficient":
        analysis = _insufficient_analysis(candidate, evidence, coverage)
        partial = {
            "duplicate_risk": duplicate_score if comparison_covered else None,
            "gap_fill": gap_score if comparison_covered else None,
            "outfit_potential": pairing_score if pairing_covered else None,
            "scene_match": None,
            "idle_risk": None,
            "budget_match": budget_match,
        }
        analysis["dimensions"] = partial
        analysis["duplicate_risk"] = partial["duplicate_risk"]
        analysis["duplicate_score"] = partial["duplicate_risk"]
        analysis["wardrobe_gap_score"] = partial["gap_fill"]
        analysis["outfit_potential"] = partial["outfit_potential"]
        analysis["pairing_score"] = partial["outfit_potential"]
        analysis["outfit_ideas"] = combinations
        analysis["similar_items"] = [item.__dict__ for item in similar_items]
        if comparison_covered or pairing_covered:
            analysis["score_breakdown"] = {**partial, "wardrobe_gap": partial["gap_fill"]}
        return PurchaseDecision(similar_items=similar_items, recommendation="consider", score=None,
                                reason_summary="资料不足：部分个人化维度缺少已确认衣物或季节依据。",
                                analysis=analysis, coverage=coverage, evidence=evidence)
    base_score = round((100 - duplicate_score) * 0.3 + gap_score * 0.35 + pairing_score * 0.35)
    preference_bonus = _preference_bonus(candidate, preferences)
    feedback_adjustment = max(-15, min(0, feedback_adjustment))
    score = max(0, min(100, base_score + preference_bonus + feedback_adjustment))

    if score >= 75 and duplicate_score < 80 and (gap_score >= 55 or pairing_score >= 70):
        recommendation = "recommend"
    elif score < 50 or (duplicate_score >= 80 and gap_score < 65):
        recommendation = "skip"
    else:
        recommendation = "consider"

    decision_factors = _decision_factors(candidate, duplicate_score, gap_score, pairing_score)
    decision_factors.append(f"confirmed_wardrobe_count:{len(ready)}")
    decision_factors.append(f"preference_adjustment:{preference_bonus}")
    decision_factors.append(f"feedback_adjustment:{feedback_adjustment}")
    if preferences:
        decision_factors.append(f"user_preferences:{json.dumps(preferences, ensure_ascii=False)}")
    reason_summary = _reason_summary(recommendation, duplicate_score, gap_score, pairing_score)
    analysis = _structured_analysis(
        candidate=candidate,
        recommendation=recommendation,
        score=score,
        duplicate_score=duplicate_score,
        gap_score=gap_score,
        pairing_score=pairing_score,
        decision_factors=decision_factors,
        similar_items=similar_items,
        outfit_ideas=combinations,
    )
    analysis["coverage"] = coverage
    analysis["evidence"] = evidence
    return PurchaseDecision(
        similar_items=similar_items,
        recommendation=recommendation,
        score=score,
        reason_summary=reason_summary,
        analysis=analysis,
        coverage=coverage,
        evidence=evidence,
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
    outfit_ideas: list[dict[str, object]],
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
        "suggested_price": None,
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
        "outfit_ideas": outfit_ideas,
        "idle_risk_detail": _idle_risk_detail(idle_risk),
        "next_actions": ["save", "share", "analyze_another", "upload_wardrobe"],
        "duplicate_score": duplicate_score,
        "wardrobe_gap_score": gap_score,
        "pairing_score": pairing_score,
        "decision_factors": decision_factors,
        "coverage": {"status": "sufficient"},
        "evidence": {"rule_version": PURCHASE_RULE_VERSION},
    }


def _insufficient_analysis(
    candidate: AiAnalysis,
    evidence: dict[str, object],
    coverage: dict[str, object],
) -> dict[str, object]:
    return {
        "conclusion": "consider",
        "score": None,
        "summary": "请先录入并确认衣物，以便比较重复度和搭配覆盖。",
        "dimensions": {
            "outfit_potential": None,
            "scene_match": None,
            "gap_fill": None,
            "duplicate_risk": None,
            "idle_risk": None,
            "budget_match": None,
        },
        "duplicate_risk": None,
        "idle_risk": None,
        "outfit_potential": None,
        "match_scenes": _match_scenes(candidate),
        "suggested_price": None,
        "score_breakdown": None,
        "pros": [],
        "cons": ["已录入衣物不足，个人化建议暂不可用。"],
        "outfit_ideas": [],
        "idle_risk_detail": None,
        "next_actions": ["upload_wardrobe", "analyze_another"],
        "duplicate_score": None,
        "wardrobe_gap_score": None,
        "pairing_score": None,
        "decision_factors": ["insufficient_confirmed_wardrobe"],
        "coverage": coverage,
        "evidence": evidence,
    }


def _hard_constraint_analysis(
    candidate: AiAnalysis,
    evidence: dict[str, object],
    coverage: dict[str, object],
    summary: str,
) -> dict[str, object]:
    analysis = _insufficient_analysis(candidate, evidence, coverage)
    analysis["conclusion"] = "skip"
    analysis["summary"] = summary
    analysis["decision_factors"] = list(evidence.get("hard_constraints", []))
    return analysis


def _numeric_price(value: object) -> float | None:
    try:
        number = float(str(value)) if value is not None else None
    except (TypeError, ValueError):
        return None
    if number is None or number < 0:
        return None
    return number


def _normalize_label(value: str) -> str:
    return " ".join(value.casefold().split())


def _preference_bonus(candidate: AiAnalysis, preferences: dict[str, object] | None) -> int:
    if not preferences:
        return 0
    bonus = 0
    styles = {_normalize_label(str(value)) for value in preferences.get("styles", [])}
    if candidate.style and _normalize_label(candidate.style) in styles:
        bonus += 5
    scenes = {_normalize_label(str(value)) for value in preferences.get("scenes", [])}
    if scenes & {_normalize_label(value) for value in _match_scenes(candidate)}:
        bonus += 5
    return bonus


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


_CORE_CATEGORIES = ("top", "bottom", "shoes")
_OPTIONAL_CATEGORIES = ("outerwear", "bag", "accessory")
_OUTFIT_CATEGORIES = {*_CORE_CATEGORIES, *_OPTIONAL_CATEGORIES}
_SEASONS = {"spring", "summer", "autumn", "winter"}


def _known_seasons(labels: list[str] | None) -> set[str]:
    aliases = {"春": "spring", "夏": "summer", "秋": "autumn", "冬": "winter",
               "fall": "autumn", "春季": "spring", "夏季": "summer", "秋季": "autumn", "冬季": "winter"}
    values = {_normalize_label(value) for value in labels or []}
    if values & {"all", "all seasons", "all-season", "四季", "四季通用"}:
        return set(_SEASONS)
    return {aliases.get(value, value) for value in values} & _SEASONS


def _season_evidence(items: list[AiAnalysis | Garment]) -> tuple[bool, list[str], list[str]]:
    known = [_known_seasons(item.season) for item in items]
    common = set(_SEASONS)
    for seasons in known:
        if seasons:
            common &= seasons
    if not common:
        return False, [], []
    if any(not seasons for seasons in known):
        return True, [], ["season_unverified"]
    return True, sorted(common), []


def _outfit_ideas(
    candidate: AiAnalysis,
    garments: list[Garment],
    scenes: list[str],
    candidate_id: str | None,
) -> list[dict[str, object]]:
    if not candidate_id or candidate.category not in _OUTFIT_CATEGORIES:
        return []
    known_seasons = {garment.id: _known_seasons(garment.season) for garment in garments}
    by_category = {
        category: sorted((garment for garment in garments if garment.category == category),
                         key=lambda garment: (not bool(known_seasons[garment.id]), garment.id))
        for category in _OUTFIT_CATEGORIES
    }
    required = [category for category in _CORE_CATEGORIES if category != candidate.category]
    if any(not by_category[category] for category in required):
        return []
    ideas: list[dict[str, object]] = []
    signatures: set[tuple[str, ...]] = set()
    candidate_seasons = _known_seasons(candidate.season)
    # Index the four possible seasons before enumeration. Every selected prefix
    # shares its bucket's season, so incompatible wardrobes never form a product.
    # Try verified groups first, then allow unknowns without dropping late rows.
    buckets = {
        season: {category: [garment for garment in values
                            if not known_seasons[garment.id] or season in known_seasons[garment.id]]
                 for category, values in by_category.items()}
        for season in sorted(candidate_seasons or _SEASONS)
    }

    def selections():
        for allow_unknown in (False, True):
            if not allow_unknown and not candidate_seasons:
                continue
            for season, categories in buckets.items():
                choices = [categories[category] if allow_unknown else
                           [garment for garment in categories[category] if known_seasons[garment.id]]
                           for category in required]
                if all(choices):
                    for selection in product(*choices):
                        yield season, list(selection)

    for season, owned in selections():
        for category in _OPTIONAL_CATEGORIES:
            if category == candidate.category:
                continue
            # Optional pieces must not turn a verified base into an unknown or
            # incompatible seasonal claim; omit them if there is no known match.
            for garment in buckets[season][category]:
                if known_seasons[garment.id]:
                    owned.append(garment)
                    break
        signature = tuple(garment.id for garment in owned)
        if signature in signatures or len(signature) != len(set(signature)):
            continue
        signatures.add(signature)
        _, seasons, warnings = _season_evidence([candidate, *owned])
        items = [{"candidate_id": candidate_id, "category": candidate.category,
                  "reason": "待购买候选单品"}]
        for garment in owned:
            ref = {"garment_id": garment.id, "category": garment.category, "reason": "来自你已确认的衣橱"}
            if garment.asset_id:
                ref["asset_id"] = garment.asset_id
            elif garment.thumbnail_url or garment.image_url:
                ref["image_url"] = garment.thumbnail_url or garment.image_url
            items.append(ref)
        ideas.append({"scene": scenes[len(ideas) % len(scenes)], "items": items,
                      "seasons": seasons, "warnings": warnings,
                      "reason": "候选与已确认的上衣、下装及鞋组成完整搭配。" +
                                (" 部分单品季节未知，季节适配尚未核验。" if warnings else " 季节标签存在共同交集。")})
        if len(ideas) == 3:
            break
    return ideas


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
