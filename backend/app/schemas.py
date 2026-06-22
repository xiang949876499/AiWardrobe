from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationInfo, field_validator

Category = Literal["top", "bottom", "outerwear", "shoes", "bag", "accessory"]
GarmentStatus = Literal["uploaded", "extracting", "tagging", "pending_review", "processing", "ready", "failed"]
PurchaseCandidateStatus = Literal["analyzing", "ready", "failed", "saved"]
PurchaseRecommendation = Literal["recommend", "consider", "skip"]
ShoppingRecommendationTarget = Literal["auto_gap", "work", "date", "sport", "summer", "basics"]
ShoppingRecommendationStatus = Literal["running", "ready", "failed", "rate_limited"]
ShoppingAnalysisStatus = Literal["pending_analysis", "analyzing", "analyzed", "failed"]


class EmailCodeRequest(BaseModel):
    email: EmailStr


class EmailCodeVerify(BaseModel):
    email: EmailStr
    code: str = Field(min_length=6, max_length=6)


class PasswordAuthRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    created_at: datetime
    updated_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class EmailCodeAccepted(BaseModel):
    message: str
    dev_code: str | None = None


class GarmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_upload_id: str | None = None
    image_url: str
    image_key: str
    thumbnail_url: str | None
    category: Category
    colors: list[str]
    style: str
    material: str
    season: list[str]
    fit: str
    tags: list[str]
    crop_box: dict[str, int] | None = None
    ai_result: dict[str, object]
    ai_confidence: float
    status: GarmentStatus
    review_status: str = "confirmed"
    created_at: datetime
    updated_at: datetime


class GarmentListResponse(BaseModel):
    items: list[GarmentResponse]


class GarmentUpdate(BaseModel):
    category: Category | None = None
    colors: list[str] | None = None
    style: str | None = None
    material: str | None = None
    season: list[str] | None = None
    fit: str | None = None
    tags: list[str] | None = None
    review_status: Literal["pending_review", "confirmed"] | None = None


class PurchaseAnalyzeRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)


class SimilarPurchaseItem(BaseModel):
    garment_id: str
    image_url: str
    similarity: float
    matched_reasons: list[str]


class PurchaseScoreBreakdown(BaseModel):
    duplicate_risk: int
    wardrobe_gap: int
    outfit_potential: int
    scene_match: int
    idle_risk: int


class PurchaseAnalysisDimensions(BaseModel):
    outfit_potential: int
    scene_match: int
    gap_fill: int
    duplicate_risk: int
    idle_risk: int


class PurchaseSuggestedPrice(BaseModel):
    min: int
    ideal: int
    max: int


class PurchaseAnalysisDetail(BaseModel):
    conclusion: PurchaseRecommendation
    score: int
    summary: str
    dimensions: PurchaseAnalysisDimensions
    duplicate_risk: int
    idle_risk: int
    outfit_potential: int
    match_scenes: list[str]
    suggested_price: PurchaseSuggestedPrice
    score_breakdown: PurchaseScoreBreakdown
    pros: list[str]
    cons: list[str]
    outfit_ideas: list[dict[str, object]]
    idle_risk_detail: dict[str, str]
    next_actions: list[str]
    duplicate_score: int
    wardrobe_gap_score: int
    pairing_score: int
    decision_factors: list[str]
    similar_items: list[SimilarPurchaseItem] = Field(default_factory=list)


class PurchaseCandidateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    product_url: str
    source_image_url: str
    image_url: str
    image_key: str
    thumbnail_url: str | None = None
    title: str
    domain: str
    category: Category
    colors: list[str]
    style: str
    material: str
    season: list[str]
    fit: str
    tags: list[str]
    ai_result: dict[str, object]
    ai_confidence: float
    similar_items: list[SimilarPurchaseItem]
    recommendation: PurchaseRecommendation
    score: int
    reason_summary: str
    analysis: PurchaseAnalysisDetail
    status: PurchaseCandidateStatus
    created_at: datetime
    updated_at: datetime

    @field_validator("analysis", mode="before")
    @classmethod
    def backfill_legacy_analysis(cls, value: object, info: ValidationInfo) -> object:
        return _normalize_purchase_analysis(value, info.data)


def _normalize_purchase_analysis(value: object, response_data: dict[str, object]) -> object:
    if isinstance(value, PurchaseAnalysisDetail):
        return value

    analysis = dict(value) if isinstance(value, dict) else {}
    raw_dimensions = analysis.get("dimensions") if isinstance(analysis.get("dimensions"), dict) else {}
    raw_breakdown = analysis.get("score_breakdown") if isinstance(analysis.get("score_breakdown"), dict) else {}
    duplicate_score = _first_analysis_int(
        analysis,
        raw_breakdown,
        raw_dimensions,
        ("duplicate_score", "duplicate_risk", "duplicate_risk", "duplicate_risk"),
    )
    gap_score = _first_analysis_int(
        analysis,
        raw_breakdown,
        raw_dimensions,
        ("wardrobe_gap_score", "gap_fill", "wardrobe_gap", "gap_fill"),
    )
    pairing_score = _first_analysis_int(
        analysis,
        raw_breakdown,
        raw_dimensions,
        ("pairing_score", "outfit_potential", "outfit_potential", "outfit_potential"),
    )
    idle_risk = _first_analysis_int(
        analysis,
        raw_breakdown,
        raw_dimensions,
        ("idle_risk", "idle_risk", "idle_risk", "idle_risk"),
        _legacy_idle_risk(duplicate_score, gap_score, pairing_score),
    )
    scene_match = _first_analysis_int(
        analysis,
        raw_breakdown,
        raw_dimensions,
        ("scene_match", "scene_match", "scene_match", "scene_match"),
        68,
    )
    score = _analysis_int(analysis, "score", int(response_data.get("score") or 0))
    recommendation = _analysis_recommendation(analysis.get("conclusion") or response_data.get("recommendation"))
    summary = str(analysis.get("summary") or response_data.get("reason_summary") or "")
    decision_factors = _analysis_str_list(analysis.get("decision_factors"))

    default_dimensions = {
        "outfit_potential": pairing_score,
        "scene_match": scene_match,
        "gap_fill": gap_score,
        "duplicate_risk": duplicate_score,
        "idle_risk": idle_risk,
    }
    dimensions = {**default_dimensions, **raw_dimensions}

    default_breakdown = {
        "duplicate_risk": duplicate_score,
        "wardrobe_gap": gap_score,
        "outfit_potential": pairing_score,
        "scene_match": scene_match,
        "idle_risk": idle_risk,
    }
    score_breakdown = {**default_breakdown, **raw_breakdown}

    analysis.setdefault("conclusion", recommendation)
    analysis.setdefault("score", score)
    analysis.setdefault("summary", summary)
    analysis["dimensions"] = dimensions
    _set_int_default_or_replace_zero(analysis, "duplicate_risk", duplicate_score)
    _set_int_default_or_replace_zero(analysis, "idle_risk", idle_risk)
    _set_int_default_or_replace_zero(analysis, "outfit_potential", pairing_score)
    analysis.setdefault("match_scenes", ["日常"])
    analysis.setdefault("suggested_price", _legacy_suggested_price(response_data.get("category"), score))
    analysis["score_breakdown"] = score_breakdown
    analysis.setdefault("pros", _legacy_pros(gap_score, pairing_score, duplicate_score))
    analysis.setdefault("cons", _legacy_cons(gap_score, pairing_score, duplicate_score))
    analysis.setdefault("outfit_ideas", [])
    analysis.setdefault("idle_risk_detail", _legacy_idle_risk_detail(idle_risk))
    analysis.setdefault("next_actions", ["save", "share", "analyze_another", "upload_wardrobe"])
    _set_int_default_or_replace_zero(analysis, "duplicate_score", duplicate_score)
    _set_int_default_or_replace_zero(analysis, "wardrobe_gap_score", gap_score)
    _set_int_default_or_replace_zero(analysis, "pairing_score", pairing_score)
    analysis.setdefault("decision_factors", decision_factors)
    analysis.setdefault("similar_items", response_data.get("similar_items") or [])
    return analysis


def _analysis_int(analysis: dict[str, object], key: str, default: int = 0) -> int:
    try:
        return int(analysis.get(key, default) or 0)
    except (TypeError, ValueError):
        return default


def _first_analysis_int(
    analysis: dict[str, object],
    score_breakdown: dict[object, object],
    dimensions: dict[object, object],
    keys: tuple[str, str, str, str],
    default: int = 0,
) -> int:
    alias_key, top_level_key, breakdown_key, dimension_key = keys
    first_zero: int | None = None
    for source, key in (
        (analysis, alias_key),
        (analysis, top_level_key),
        (score_breakdown, breakdown_key),
        (dimensions, dimension_key),
    ):
        if key in source and source[key] is not None:
            try:
                value = int(source[key] or 0)
            except (TypeError, ValueError):
                continue
            if value != 0:
                return value
            if first_zero is None:
                first_zero = value
    return first_zero if first_zero is not None else default


def _set_int_default_or_replace_zero(analysis: dict[str, object], key: str, value: int) -> None:
    if key not in analysis or analysis[key] is None:
        analysis[key] = value
        return
    try:
        current = int(analysis[key] or 0)
    except (TypeError, ValueError):
        return
    if current == 0 and value != 0:
        analysis[key] = value


def _analysis_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _analysis_recommendation(value: object) -> PurchaseRecommendation:
    if value in {"recommend", "consider", "skip"}:
        return value
    return "consider"


def _legacy_idle_risk(duplicate_score: int, gap_score: int, pairing_score: int) -> int:
    risk = round(duplicate_score * 0.45 + (100 - gap_score) * 0.3 + (100 - pairing_score) * 0.25)
    return max(0, min(100, risk))


def _legacy_suggested_price(category: object, score: int) -> dict[str, int]:
    base = 180 if category in {"outerwear", "shoes", "bag"} else 120
    ideal = round(base * (0.75 + score / 200))
    return {"min": max(49, ideal - 70), "ideal": max(1, ideal), "max": ideal + 100}


def _legacy_pros(gap_score: int, pairing_score: int, duplicate_score: int) -> list[str]:
    pros: list[str] = []
    if gap_score >= 65:
        pros.append("补足衣橱缺口")
    if pairing_score >= 70:
        pros.append("搭配潜力高")
    if duplicate_score < 70:
        pros.append("与现有衣橱区分明显")
    return pros or ["可继续观察"]


def _legacy_cons(gap_score: int, pairing_score: int, duplicate_score: int) -> list[str]:
    cons: list[str] = []
    if gap_score < 65:
        cons.append("新增覆盖有限")
    if pairing_score < 70:
        cons.append("可搭配方案偏少")
    if duplicate_score >= 70:
        cons.append("已有相似单品")
    return cons or ["暂无明显风险"]


def _legacy_idle_risk_detail(idle_risk: int) -> dict[str, str]:
    if idle_risk >= 70:
        return {"level": "高", "reason": "重复或不好搭的概率偏高，建议冷静后再买。"}
    if idle_risk >= 45:
        return {"level": "中", "reason": "有一定使用场景，但需要确认价格和搭配。"}
    return {"level": "低", "reason": "与衣橱互补度较好，闲置风险可控。"}


class ShoppingRateLimitResponse(BaseModel):
    remaining_refreshes: int | None = None
    reset_at: datetime | None = None


class ShoppingRecommendationRequest(BaseModel):
    target: ShoppingRecommendationTarget = "auto_gap"
    refresh: bool = False


class ShoppingRecommendationItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    platform: str
    platform_item_id: str
    title: str
    image_url: str
    price: str
    shop_name: str
    product_url: str
    analysis_status: ShoppingAnalysisStatus
    purchase_candidate_id: str | None = None
    recommendation: PurchaseRecommendation | None = None
    score: int | None = None
    reason_summary: str
    similar_items: list[SimilarPurchaseItem] = []


class ShoppingRecommendationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    target: ShoppingRecommendationTarget
    keywords: list[str]
    status: ShoppingRecommendationStatus
    error_code: str | None = None
    cache_hit: bool
    rate_limit: ShoppingRateLimitResponse
    items: list[ShoppingRecommendationItemResponse]
    created_at: datetime
    updated_at: datetime


class OutfitGenerateRequest(BaseModel):
    occasion: Literal["work", "date", "sport", "formal", "casual"]
    season: str = ""
    temperature: int | None = None
    weather: dict[str, object] | None = None


class OutfitItemResponse(BaseModel):
    garment_id: str
    category: str
    image_url: str
    reason: str


class OutfitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str = ""
    occasion: str
    season: str
    temperature: int | None
    items: list[dict[str, object]]
    explanation: str
    source: str = "ai"
    is_favorite: bool
    is_fixed: bool = False
    weather_snapshot: dict[str, object] | None = None
    created_at: datetime


class OutfitListResponse(BaseModel):
    items: list[OutfitResponse]


class FavoriteUpdate(BaseModel):
    is_favorite: bool


class FixedUpdate(BaseModel):
    is_fixed: bool


class ManualOutfitRequest(BaseModel):
    name: str = ""
    garment_ids: list[str] = Field(min_length=1)
    occasion: Literal["work", "date", "sport", "formal", "casual"] = "casual"
    season: str = ""
    temperature: int | None = None
    is_fixed: bool = False
    weather: dict[str, object] | None = None


class UploadSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    original_image_url: str
    original_image_key: str
    status: str
    error_message: str | None
    garments: list[GarmentResponse]
    created_at: datetime
    updated_at: datetime


class WeatherResponse(BaseModel):
    date: str
    lat_key: str
    lon_key: str
    city: str
    condition: str
    temperature: int
    feels_like: int
    precipitation: float
    wind_speed: float
    cached: bool
