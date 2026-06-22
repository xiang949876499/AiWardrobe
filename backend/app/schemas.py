from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

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
