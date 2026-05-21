from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Category = Literal["top", "bottom", "outerwear", "shoes", "accessory"]
GarmentStatus = Literal["uploaded", "extracting", "tagging", "pending_review", "processing", "ready", "failed"]


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
