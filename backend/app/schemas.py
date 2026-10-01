from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationInfo, field_validator

from app.purchase_analysis import _idle_risk, _idle_risk_detail, _pros_cons, _suggested_price

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


class UserPreferenceUpdate(BaseModel):
    primary_goal: str = ""
    scenes: list[str] = Field(default_factory=list)
    styles: list[str] = Field(default_factory=list)
    avoid_types: list[str] = Field(default_factory=list)
    budget_range: str = ""


class UserPreferenceResponse(UserPreferenceUpdate):
    model_config = ConfigDict(from_attributes=True)


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


class DistributionItem(BaseModel):
    key: str
    label: str
    count: int
    ratio: float


class WardrobeGapResponse(BaseModel):
    category: Category
    label: str
    score: int
    reason: str


class DuplicateRiskResponse(BaseModel):
    category: Category
    label: str
    colors: list[str]
    count: int
    garment_ids: list[str]


class LowUseItemResponse(BaseModel):
    garment_id: str
    category: Category
    label: str
    reason: str
    score: int


class WardrobeReportResponse(BaseModel):
    total: int
    ready_total: int
    summary: str
    category_distribution: list[DistributionItem]
    color_distribution: list[DistributionItem]
    style_distribution: list[DistributionItem]
    scene_coverage: dict[str, int]
    duplicate_risks: list[DuplicateRiskResponse]
    low_use_items: list[LowUseItemResponse]
    wardrobe_gaps: list[WardrobeGapResponse]
    avoid_categories: list[str]
    suggested_categories: list[str]


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


class PurchaseProductPrice(BaseModel):
    current: str
    currency: str = "CNY"
    source: str = "product_page"


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
    product_price: PurchaseProductPrice | None = None
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
        _idle_risk(duplicate_score, gap_score, pairing_score),
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
    default_pros, default_cons = _pros_cons(recommendation, duplicate_score, gap_score, pairing_score)
    analysis.setdefault("pros", default_pros)
    analysis.setdefault("cons", default_cons)
    analysis.setdefault("outfit_ideas", [])
    analysis["idle_risk_detail"] = _normalize_idle_risk_detail(analysis.get("idle_risk_detail"), idle_risk)
    analysis.setdefault("next_actions", ["save", "share", "analyze_another", "upload_wardrobe"])
    _set_int_default_or_replace_zero(analysis, "duplicate_score", duplicate_score)
    _set_int_default_or_replace_zero(analysis, "wardrobe_gap_score", gap_score)
    _set_int_default_or_replace_zero(analysis, "pairing_score", pairing_score)
    analysis.setdefault("decision_factors", decision_factors)
    analysis.setdefault("similar_items", response_data.get("similar_items") or [])
    return analysis


def _analysis_int(analysis: dict[str, object], key: str, default: int = 0) -> int:
    try:
        value = analysis.get(key)
        if value is None:
            return default
        return int(value or 0)
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


def _normalize_idle_risk_detail(value: object, idle_risk: int) -> dict[str, str]:
    fallback = _idle_risk_detail(idle_risk)
    if not isinstance(value, dict):
        return fallback

    normalized_level = _normalize_idle_risk_level(value.get("level"))
    if normalized_level is None:
        return fallback

    reason = value.get("reason")
    return {"level": normalized_level, "reason": str(reason) if reason else fallback["reason"]}


def _normalize_idle_risk_level(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    if normalized in {"low", "低"}:
        return "低"
    if normalized in {"medium", "mid", "中"}:
        return "中"
    if normalized in {"high", "高"}:
        return "高"
    return None


def _analysis_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _analysis_recommendation(value: object) -> PurchaseRecommendation:
    if value in {"recommend", "consider", "skip"}:
        return value
    return "consider"


def _legacy_suggested_price(category: object, score: int) -> dict[str, int]:
    """Thin wrapper delegating to _suggested_price — accepts raw category string for legacy backfill."""
    base = 180 if category in {"outerwear", "shoes", "bag"} else 120
    ideal = round(base * (0.75 + score / 200))
    return {"min": max(49, ideal - 70), "ideal": max(1, ideal), "max": ideal + 100}


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


class RecommendationGroupResponse(BaseModel):
    title: str
    reason: str
    item_ids: list[str]


class ShoppingRecommendationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    target: ShoppingRecommendationTarget
    keywords: list[str]
    status: ShoppingRecommendationStatus
    error_code: str | None = None
    cache_hit: bool
    rate_limit: ShoppingRateLimitResponse
    wardrobe_gaps: list[WardrobeGapResponse] = []
    avoid_categories: list[str] = []
    recommendation_groups: list[RecommendationGroupResponse] = []
    items: list[ShoppingRecommendationItemResponse]
    created_at: datetime
    updated_at: datetime


class OutfitGenerateRequest(BaseModel):
    occasion: Literal["work", "date", "sport", "formal", "casual"]
    season: str = ""
    temperature: int | None = None
    weather: dict[str, object] | None = None
    garment_id: str | None = None
    purchase_candidate_id: str | None = None


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


WeddingStyle = Literal["warm_documentary", "romantic_film", "fresh_illustration"]
WeddingPersonKey = Literal["A", "B"]


class WeddingProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    owner_name: str = Field(min_length=1, max_length=20)
    style: WeddingStyle = "warm_documentary"


class WeddingProjectPatch(BaseModel):
    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=120)
    style: WeddingStyle | None = None


class WeddingProjectResponse(BaseModel):
    id: str
    title: str
    status: str
    version: int
    style: WeddingStyle
    target_duration_ms: int
    aspect_ratio: str
    budget_limit_fen: int
    budget_reserved_fen: int
    budget_spent_fen: int
    created_at: datetime
    updated_at: datetime


class WeddingParticipantSummary(BaseModel):
    person_key: WeddingPersonKey
    display_name: str
    consent_status: Literal["pending", "active", "revoked", "declined"]
    confirmed_at: datetime | None = None


class WeddingProjectDetailResponse(WeddingProjectResponse):
    participants: list[WeddingParticipantSummary]
    story_text: str | None = None


class WeddingConsentRequest(BaseModel):
    terms_version: str = Field(min_length=1, max_length=64)
    scope: list[Literal["portrait_processing", "preview_review", "storage"]] = Field(min_length=1)
    provider_list: list[str] = Field(min_length=1, max_length=8)


class ConsentInviteCreate(BaseModel):
    person_key: Literal["B"] = "B"
    display_name: str = Field(min_length=1, max_length=20)
    provider_list: list[str] = Field(min_length=1, max_length=8)
    terms_version: str = Field(min_length=1, max_length=64)


class ConsentInviteResponse(BaseModel):
    id: str
    invite_token: str
    person_key: WeddingPersonKey
    expires_at: datetime


class ConsentConfirmRequest(BaseModel):
    accepted: bool
    display_name: str | None = Field(default=None, max_length=20)
    terms_version: str = Field(min_length=1, max_length=64)


class ParticipantSessionResponse(BaseModel):
    access_token: str | None = None
    token_type: str = "bearer"
    accepted: bool
    project_id: str
    participant: WeddingParticipantSummary


class WeddingAssetResponse(BaseModel):
    id: str
    asset_type: str
    person_key: WeddingPersonKey | None = None
    mime: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    generated: bool
    created_at: datetime


class StoryFactInput(BaseModel):
    source_field: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1000)


class StoryFactResponse(BaseModel):
    id: str
    source_field: str
    text: str
    confirmed_at: datetime | None = None


class StoryboardShotInput(BaseModel):
    shot_index: int = Field(ge=1, le=18)
    shot_type: Literal["person_ai", "scene_ai", "photo"]
    duration_ms: int = Field(default=5000, ge=1000, le=10000)
    person_keys: list[WeddingPersonKey] = Field(default_factory=list, max_length=2)
    fact_indexes: list[int] = Field(default_factory=list, max_length=10)
    description: str = Field(min_length=1, max_length=1000)
    subtitle: str = Field(default="", max_length=80)
    creative_scene: bool = False


class WeddingShotReviewSummary(BaseModel):
    person_key: WeddingPersonKey
    asset_id: str
    result: Literal["approved", "rejected"]
    confirmed_at: datetime


class StoryboardShotResponse(BaseModel):
    id: str
    project_version: int
    shot_index: int
    shot_type: str
    duration_ms: int
    person_keys: list[WeddingPersonKey]
    fact_ids: list[str]
    description: str
    subtitle: str
    creative_scene: bool
    frame_asset_id: str | None = None
    selected_asset_id: str | None = None
    selected_candidate_id: str | None = None
    status: str
    identity_reviews: list[WeddingShotReviewSummary] = Field(default_factory=list)


class StoryboardSubmitRequest(BaseModel):
    version: int = Field(ge=1)
    story_text: str = Field(min_length=1, max_length=12000)
    facts: list[StoryFactInput] = Field(min_length=1, max_length=40)
    shots: list[StoryboardShotInput] = Field(min_length=1, max_length=18)


class StoryVersionResponse(BaseModel):
    id: str
    version: int
    story_text: str
    style: WeddingStyle
    status: str
    owner_approved_at: datetime | None = None
    facts: list[StoryFactResponse]
    shots: list[StoryboardShotResponse]


class VersionRequest(BaseModel):
    version: int = Field(ge=1)


class FrameAssignment(BaseModel):
    shot_id: str
    asset_id: str


class FrameApprovalRequest(BaseModel):
    version: int = Field(ge=1)
    frames: list[FrameAssignment] = Field(min_length=1, max_length=18)


class ParticipantReviewRequest(BaseModel):
    asset_id: str
    result: Literal["approved", "rejected"]
    project_version: int = Field(ge=1)


class ParticipantReviewResponse(BaseModel):
    id: str
    person_key: WeddingPersonKey
    project_version: int
    shot_id: str | None = None
    asset_id: str
    result: Literal["approved", "rejected"]
    confirmed_at: datetime


class WeddingQuoteResponse(BaseModel):
    id: str
    project_version: int
    max_budget_fen: int
    currency: Literal["CNY"] = "CNY"
    price_version: str
    issued_at: datetime
    expires_at: datetime


class GenerationJobCreate(BaseModel):
    quote_id: str
    version: int = Field(ge=1)


class GenerationJobResponse(BaseModel):
    id: str
    project_id: str
    project_version: int
    status: Literal["queued", "processing", "needs_action", "completed", "failed", "cancel_requested", "cancelled"]
    progress_stage: str
    completed_shots: int
    total_shots: int
    reserved_amount_fen: int
    actual_cost_fen: int
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class GenerationJobCancelResponse(BaseModel):
    id: str
    status: str


class CandidateSelectionRequest(BaseModel):
    project_version: int = Field(ge=1)
    candidate_id: str


class WeddingCandidateResponse(BaseModel):
    id: str
    shot_id: str
    generation_job_id: str
    asset_id: str
    asset: WeddingAssetResponse
    provider: str
    model_version: str
    quality_flags: list[str]
    review_result: Literal["pending", "approved", "rejected"]
    actual_cost_fen: int
    created_at: datetime


class WeddingProviderAvailability(BaseModel):
    provider: str
    configured: bool
    available: bool
    reason: str


class WeddingCapabilitiesResponse(BaseModel):
    mode: Literal["disabled", "demo"]
    demo_only: bool
    providers: dict[str, WeddingProviderAvailability]
    ffmpeg_available: bool
    licensed_music_available: bool
    real_export_available: bool


class FallbackAcceptRequest(BaseModel):
    project_version: int = Field(ge=1)
    asset_id: str


class RenderCreateRequest(BaseModel):
    version: int = Field(ge=1)


class WeddingRenderReviewSummary(BaseModel):
    person_key: WeddingPersonKey
    asset_id: str
    result: Literal["approved", "rejected"]
    confirmed_at: datetime


class RenderVersionResponse(BaseModel):
    id: str
    project_version: int
    version: int
    status: Literal["queued", "processing", "ready", "approved", "failed", "cancelled"]
    preview_asset_id: str | None = None
    export_asset_id: str | None = None
    output_duration_ms: int | None = None
    owner_approved_at: datetime | None = None
    created_at: datetime
    identity_reviews: list[WeddingRenderReviewSummary] = Field(default_factory=list)


class ExportResponse(BaseModel):
    render_version_id: str
    asset_id: str
    mime: str
    size_bytes: int
