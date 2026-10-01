from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Enum, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    garments: Mapped[list["Garment"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    outfits: Mapped[list["Outfit"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    uploads: Mapped[list["UploadSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    purchase_candidates: Mapped[list["PurchaseCandidate"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    shopping_recommendation_runs: Mapped[list["ShoppingRecommendationRun"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    shopping_recommendation_items: Mapped[list["ShoppingRecommendationItem"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    preference: Mapped["UserPreference | None"] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, index=True, nullable=False)
    primary_goal: Mapped[str] = mapped_column(String(120), default="")
    scenes: Mapped[list[str]] = mapped_column(JSON, default=list)
    styles: Mapped[list[str]] = mapped_column(JSON, default=list)
    avoid_types: Mapped[list[str]] = mapped_column(JSON, default=list)
    budget_range: Mapped[str] = mapped_column(String(80), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user: Mapped[User] = relationship(back_populates="preference")


class EmailCode(Base):
    __tablename__ = "email_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Garment(Base):
    __tablename__ = "garments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    source_upload_id: Mapped[str | None] = mapped_column(ForeignKey("upload_sessions.id"), nullable=True)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_key: Mapped[str] = mapped_column(String(512), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    colors: Mapped[list[str]] = mapped_column(JSON, default=list)
    style: Mapped[str] = mapped_column(String(120), default="")
    material: Mapped[str] = mapped_column(String(120), default="")
    season: Mapped[list[str]] = mapped_column(JSON, default=list)
    fit: Mapped[str] = mapped_column(String(120), default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    crop_box: Mapped[dict[str, int] | None] = mapped_column(JSON, nullable=True)
    ai_result: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    ai_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), index=True, default="uploaded")
    review_status: Mapped[str] = mapped_column(String(32), index=True, default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user: Mapped[User] = relationship(back_populates="garments")
    upload_session: Mapped["UploadSession | None"] = relationship(back_populates="garments")


class PurchaseCandidate(Base):
    __tablename__ = "purchase_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    product_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_image_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_key: Mapped[str] = mapped_column(String(512), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(String(240), default="")
    domain: Mapped[str] = mapped_column(String(240), default="")
    category: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    colors: Mapped[list[str]] = mapped_column(JSON, default=list)
    style: Mapped[str] = mapped_column(String(120), default="")
    material: Mapped[str] = mapped_column(String(120), default="")
    season: Mapped[list[str]] = mapped_column(JSON, default=list)
    fit: Mapped[str] = mapped_column(String(120), default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    ai_result: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    ai_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    similar_items: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    recommendation: Mapped[str] = mapped_column(String(32), index=True, default="consider")
    score: Mapped[int] = mapped_column(Integer, default=0)
    reason_summary: Mapped[str] = mapped_column(Text, default="")
    analysis: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), index=True, default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user: Mapped[User] = relationship(back_populates="purchase_candidates")


class ShoppingRecommendationRun(Base):
    __tablename__ = "shopping_recommendation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    target: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(32), index=True, default="running")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    rate_limit: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user: Mapped[User] = relationship(back_populates="shopping_recommendation_runs")
    items: Mapped[list["ShoppingRecommendationItem"]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
    )


class ShoppingRecommendationItem(Base):
    __tablename__ = "shopping_recommendation_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    run_id: Mapped[str] = mapped_column(ForeignKey("shopping_recommendation_runs.id"), index=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    platform: Mapped[str] = mapped_column(String(32), index=True, default="taobao")
    platform_item_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(300), default="")
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[str] = mapped_column(String(40), default="")
    shop_name: Mapped[str] = mapped_column(String(160), default="")
    product_url: Mapped[str] = mapped_column(Text, nullable=False)
    raw: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    analysis_status: Mapped[str] = mapped_column(String(32), index=True, default="pending_analysis")
    purchase_candidate_id: Mapped[str | None] = mapped_column(ForeignKey("purchase_candidates.id"), nullable=True)
    recommendation: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    run: Mapped[ShoppingRecommendationRun] = relationship(back_populates="items")
    user: Mapped[User] = relationship(back_populates="shopping_recommendation_items")
    purchase_candidate: Mapped[PurchaseCandidate | None] = relationship()

    @property
    def similar_items(self) -> list[dict[str, object]]:
        if self.purchase_candidate is None:
            return []
        return self.purchase_candidate.similar_items or []


class RateLimitEvent(Base):
    __tablename__ = "rate_limit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    scope: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    key: Mapped[str] = mapped_column(String(160), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    original_image_url: Mapped[str] = mapped_column(Text, nullable=False)
    original_image_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, default="uploaded")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user: Mapped[User] = relationship(back_populates="uploads")
    garments: Mapped[list[Garment]] = relationship(back_populates="upload_session", cascade="all, delete-orphan")


class Outfit(Base):
    __tablename__ = "outfits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), default="")
    occasion: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    season: Mapped[str] = mapped_column(String(64), default="")
    temperature: Mapped[int | None] = mapped_column(Integer, nullable=True)
    items: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), index=True, default="ai")
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    is_fixed: Mapped[bool] = mapped_column(Boolean, default=False)
    weather_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="outfits")


class WeatherCache(Base):
    __tablename__ = "weather_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    date: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    lat_key: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    lon_key: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    city: Mapped[str] = mapped_column(String(120), default="")
    condition: Mapped[str] = mapped_column(String(120), default="")
    temperature: Mapped[int] = mapped_column(Integer, default=22)
    feels_like: Mapped[int] = mapped_column(Integer, default=22)
    precipitation: Mapped[float] = mapped_column(Float, default=0.0)
    wind_speed: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WeddingProjectStatus(StrEnum):
    DRAFT = "draft"
    AWAITING_CONSENT = "awaiting_consent"
    STORY_REVIEW = "story_review"
    FRAME_REVIEW = "frame_review"
    READY_TO_GENERATE = "ready_to_generate"
    GENERATING = "generating"
    PREVIEW_REVIEW = "preview_review"
    READY_TO_EXPORT = "ready_to_export"
    DELIVERED = "delivered"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class WeddingAssetType(StrEnum):
    PHOTO = "photo"
    STORYBOARD_FRAME = "storyboard_frame"
    GENERATED_FRAME = "generated_frame"
    VIDEO_CANDIDATE = "video_candidate"
    PREVIEW_VIDEO = "preview_video"
    FINAL_VIDEO = "final_video"
    MUSIC = "music"


class WeddingJobStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_ACTION = "needs_action"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"


class WeddingConsentStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DECLINED = "declined"
    REVOKED = "revoked"


class WeddingInviteStatus(StrEnum):
    ACTIVE = "active"
    CONSUMED = "consumed"
    REVOKED = "revoked"
    EXPIRED = "expired"


class StoryVersionStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


class StoryboardShotStatus(StrEnum):
    DRAFT = "draft"
    FRAME_PENDING = "frame_pending"
    FRAME_APPROVED = "frame_approved"
    GENERATING = "generating"
    CANDIDATE_REVIEW = "candidate_review"
    APPROVED = "approved"
    FALLBACK = "fallback"
    FAILED = "failed"


class ParticipantReviewStatus(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class CandidateReviewStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class RenderVersionStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    READY = "ready"
    APPROVED = "approved"
    FAILED = "failed"
    CANCELLED = "cancelled"


class BudgetLedgerStatus(StrEnum):
    RESERVED = "reserved"
    SETTLED = "settled"
    RELEASED = "released"


class WeddingProject(Base):
    __tablename__ = "wedding_projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[WeddingProjectStatus] = mapped_column(
        Enum(WeddingProjectStatus, native_enum=False, create_constraint=True, length=32),
        default=WeddingProjectStatus.DRAFT,
        index=True,
        nullable=False,
    )
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    style: Mapped[str] = mapped_column(String(32), default="warm_documentary", nullable=False)
    target_duration_ms: Mapped[int] = mapped_column(Integer, default=90_000, nullable=False)
    aspect_ratio: Mapped[str] = mapped_column(String(8), default="16:9", nullable=False)
    selected_music: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    frame_approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_limit_fen: Mapped[int] = mapped_column(Integer, default=15_000, nullable=False)
    budget_delivery_reserve_fen: Mapped[int] = mapped_column(Integer, default=2_000, nullable=False)
    budget_reserved_fen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    budget_spent_fen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    participants: Mapped[list["WeddingParticipant"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    invites: Mapped[list["ConsentInvite"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    consents: Mapped[list["WeddingConsent"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    assets: Mapped[list["WeddingAsset"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    story_versions: Mapped[list["StoryVersion"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    generation_jobs: Mapped[list["GenerationJob"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    render_versions: Mapped[list["RenderVersion"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    quotes: Mapped[list["WeddingQuote"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    budget_entries: Mapped[list["BudgetLedger"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    audit_events: Mapped[list["WeddingAuditEvent"]] = relationship(back_populates="project", cascade="all, delete-orphan")
    creation_jobs: Mapped[list["WeddingCreationJob"]] = relationship(cascade="all, delete-orphan")


class WeddingParticipant(Base):
    __tablename__ = "wedding_participants"
    __table_args__ = (UniqueConstraint("project_id", "person_key", name="uq_wedding_participant_person"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("wedding_projects.id"), index=True, nullable=False)
    person_key: Mapped[str] = mapped_column(String(1), nullable=False)
    display_name: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    primary_photo_asset_id: Mapped[str | None] = mapped_column(ForeignKey("wedding_assets.id", ondelete="SET NULL", use_alter=True), nullable=True)
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    consent_status: Mapped[WeddingConsentStatus] = mapped_column(
        Enum(WeddingConsentStatus, native_enum=False, create_constraint=True, values_callable=lambda values: [item.value for item in values], length=16),
        default=WeddingConsentStatus.PENDING,
        index=True,
        nullable=False,
    )
    consent_scope: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    provider_list: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    terms_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    session_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    session_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    project: Mapped[WeddingProject] = relationship(back_populates="participants")
    consent_records: Mapped[list["WeddingConsent"]] = relationship(back_populates="participant", cascade="all, delete-orphan")


class ConsentInvite(Base):
    __tablename__ = "wedding_consent_invites"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("wedding_projects.id"), index=True, nullable=False)
    participant_id: Mapped[str] = mapped_column(ForeignKey("wedding_participants.id"), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    scope: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    provider_list: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    terms_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[WeddingInviteStatus] = mapped_column(
        Enum(WeddingInviteStatus, native_enum=False, create_constraint=True, values_callable=lambda values: [item.value for item in values], length=16),
        default=WeddingInviteStatus.ACTIVE,
        index=True,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    project: Mapped[WeddingProject] = relationship(back_populates="invites")
    participant: Mapped[WeddingParticipant] = relationship()


class WeddingConsent(Base):
    __tablename__ = "wedding_consents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("wedding_projects.id"), index=True, nullable=False)
    participant_id: Mapped[str] = mapped_column(ForeignKey("wedding_participants.id"), index=True, nullable=False)
    scope: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    provider_list: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    terms_version: Mapped[str] = mapped_column(String(64), nullable=False)
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[WeddingProject] = relationship(back_populates="consents")
    participant: Mapped[WeddingParticipant] = relationship(back_populates="consent_records")


class WeddingAsset(Base):
    __tablename__ = "wedding_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("wedding_projects.id"), index=True, nullable=False)
    uploaded_by_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    person_key: Mapped[str | None] = mapped_column(String(1), index=True, nullable=True)
    asset_type: Mapped[WeddingAssetType] = mapped_column(
        Enum(WeddingAssetType, native_enum=False, create_constraint=True, length=32),
        default=WeddingAssetType.PHOTO,
        index=True,
        nullable=False,
    )
    object_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mime: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    quality_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    generated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    project: Mapped[WeddingProject] = relationship(back_populates="assets")


class StoryVersion(Base):
    __tablename__ = "wedding_story_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_wedding_story_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("wedding_projects.id"), index=True, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    story_text: Mapped[str] = mapped_column(Text, nullable=False)
    style: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[StoryVersionStatus] = mapped_column(
        Enum(StoryVersionStatus, native_enum=False, create_constraint=True, values_callable=lambda values: [item.value for item in values], length=16),
        default=StoryVersionStatus.DRAFT,
        index=True,
        nullable=False,
    )
    owner_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    project: Mapped[WeddingProject] = relationship(back_populates="story_versions")
    facts: Mapped[list["StoryFact"]] = relationship(back_populates="story_version", cascade="all, delete-orphan")
    shots: Mapped[list["StoryboardShot"]] = relationship(back_populates="story_version", cascade="all, delete-orphan")


class StoryFact(Base):
    __tablename__ = "wedding_story_facts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    story_version_id: Mapped[str] = mapped_column(ForeignKey("wedding_story_versions.id"), index=True, nullable=False)
    source_field: Mapped[str] = mapped_column(String(80), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    story_version: Mapped[StoryVersion] = relationship(back_populates="facts")


class StoryboardShot(Base):
    __tablename__ = "wedding_storyboard_shots"
    __table_args__ = (
        UniqueConstraint("story_version_id", "shot_index", name="uq_wedding_shot_index"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("wedding_projects.id"), index=True, nullable=False)
    story_version_id: Mapped[str] = mapped_column(ForeignKey("wedding_story_versions.id"), index=True, nullable=False)
    project_version: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    shot_index: Mapped[int] = mapped_column(Integer, nullable=False)
    shot_type: Mapped[str] = mapped_column(String(24), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    person_keys: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    fact_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    subtitle: Mapped[str] = mapped_column(String(160), default="", nullable=False)
    creative_scene: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    frame_asset_id: Mapped[str | None] = mapped_column(ForeignKey("wedding_assets.id"), nullable=True)
    selected_asset_id: Mapped[str | None] = mapped_column(ForeignKey("wedding_assets.id"), nullable=True)
    selected_candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[StoryboardShotStatus] = mapped_column(
        Enum(StoryboardShotStatus, native_enum=False, create_constraint=True, values_callable=lambda values: [item.value for item in values], length=24),
        default=StoryboardShotStatus.DRAFT,
        index=True,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    story_version: Mapped[StoryVersion] = relationship(back_populates="shots")
    frame_asset: Mapped[WeddingAsset | None] = relationship(foreign_keys=[frame_asset_id])
    selected_asset: Mapped[WeddingAsset | None] = relationship(foreign_keys=[selected_asset_id])
    reviews: Mapped[list["ParticipantReview"]] = relationship(back_populates="shot", cascade="all, delete-orphan")
    candidates: Mapped[list["ShotCandidate"]] = relationship(back_populates="shot", foreign_keys="ShotCandidate.shot_id", cascade="all, delete-orphan")


class ParticipantReview(Base):
    __tablename__ = "wedding_participant_reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    participant_id: Mapped[str] = mapped_column(ForeignKey("wedding_participants.id"), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(ForeignKey("wedding_projects.id"), index=True, nullable=False)
    person_key: Mapped[str] = mapped_column(String(1), nullable=False)
    project_version: Mapped[int] = mapped_column(Integer, nullable=False)
    shot_id: Mapped[str | None] = mapped_column(ForeignKey("wedding_storyboard_shots.id"), nullable=True)
    render_version_id: Mapped[str | None] = mapped_column(ForeignKey("wedding_render_versions.id"), nullable=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("wedding_assets.id"), nullable=False)
    result: Mapped[ParticipantReviewStatus] = mapped_column(
        Enum(ParticipantReviewStatus, native_enum=False, create_constraint=True, values_callable=lambda values: [item.value for item in values], length=16),
        nullable=False,
    )
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    carried_from_review_id: Mapped[str | None] = mapped_column(ForeignKey("wedding_participant_reviews.id"), nullable=True)

    shot: Mapped[StoryboardShot | None] = relationship(back_populates="reviews", foreign_keys=[shot_id])


class WeddingQuote(Base):
    __tablename__ = "wedding_quotes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("wedding_projects.id"), index=True, nullable=False)
    project_version: Mapped[int] = mapped_column(Integer, nullable=False)
    max_budget_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    price_version: Mapped[str] = mapped_column(String(40), default="wedding-mv-v1", nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[WeddingProject] = relationship(back_populates="quotes")


class GenerationJob(Base):
    __tablename__ = "wedding_generation_jobs"
    __table_args__ = (UniqueConstraint("project_id", "idempotency_key", name="uq_wedding_job_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("wedding_projects.id"), index=True, nullable=False)
    story_version_id: Mapped[str] = mapped_column(ForeignKey("wedding_story_versions.id"), nullable=False)
    quote_id: Mapped[str] = mapped_column(ForeignKey("wedding_quotes.id"), nullable=False)
    target_shot_id: Mapped[str | None] = mapped_column(ForeignKey("wedding_storyboard_shots.id"), nullable=True)
    project_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[WeddingJobStatus] = mapped_column(
        Enum(WeddingJobStatus, native_enum=False, create_constraint=True, length=32),
        default=WeddingJobStatus.QUEUED,
        index=True,
        nullable=False,
    )
    progress_stage: Mapped[str] = mapped_column(String(40), default="queued", nullable=False)
    completed_shots: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_shots: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    reserved_amount_fen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    actual_cost_fen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_category: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)

    project: Mapped[WeddingProject] = relationship(back_populates="generation_jobs")
    candidates: Mapped[list["ShotCandidate"]] = relationship(back_populates="job", cascade="all, delete-orphan")
    budget_entry: Mapped["BudgetLedger | None"] = relationship(back_populates="job", cascade="all, delete-orphan", uselist=False)


class ShotCandidate(Base):
    __tablename__ = "wedding_shot_candidates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("wedding_projects.id"), index=True, nullable=False)
    shot_id: Mapped[str] = mapped_column(ForeignKey("wedding_storyboard_shots.id"), index=True, nullable=False)
    generation_job_id: Mapped[str] = mapped_column(ForeignKey("wedding_generation_jobs.id"), index=True, nullable=False)
    asset_id: Mapped[str] = mapped_column(ForeignKey("wedding_assets.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model_version: Mapped[str] = mapped_column(String(120), nullable=False)
    quality_flags: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    review_result: Mapped[CandidateReviewStatus] = mapped_column(
        Enum(CandidateReviewStatus, native_enum=False, create_constraint=True, values_callable=lambda values: [item.value for item in values], length=16),
        default=CandidateReviewStatus.PENDING,
        nullable=False,
    )
    actual_cost_fen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    shot: Mapped[StoryboardShot] = relationship(back_populates="candidates", foreign_keys=[shot_id])
    job: Mapped[GenerationJob] = relationship(back_populates="candidates")


class RenderVersion(Base):
    __tablename__ = "wedding_render_versions"
    __table_args__ = (UniqueConstraint("project_id", "version", name="uq_wedding_render_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("wedding_projects.id"), index=True, nullable=False)
    project_version: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[RenderVersionStatus] = mapped_column(
        Enum(RenderVersionStatus, native_enum=False, create_constraint=True, values_callable=lambda values: [item.value for item in values], length=16),
        default=RenderVersionStatus.QUEUED,
        index=True,
        nullable=False,
    )
    timeline_snapshot: Mapped[list[dict]] = mapped_column(JSON, default=list, nullable=False)
    music_snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    restored_from_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    timeline_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_asset_ids: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    preview_asset_id: Mapped[str | None] = mapped_column(ForeignKey("wedding_assets.id"), nullable=True)
    export_asset_id: Mapped[str | None] = mapped_column(ForeignKey("wedding_assets.id"), nullable=True)
    output_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    owner_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    project: Mapped[WeddingProject] = relationship(back_populates="render_versions")
    participant_reviews: Mapped[list[ParticipantReview]] = relationship(
        primaryjoin="RenderVersion.id == foreign(ParticipantReview.render_version_id)",
        cascade="all, delete-orphan",
    )


class BudgetLedger(Base):
    __tablename__ = "wedding_budget_ledger"
    __table_args__ = (
        UniqueConstraint("project_id", "reservation_key", name="uq_wedding_budget_reservation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("wedding_projects.id"), index=True, nullable=False)
    generation_job_id: Mapped[str] = mapped_column(ForeignKey("wedding_generation_jobs.id"), unique=True, nullable=False)
    reservation_key: Mapped[str] = mapped_column(String(140), nullable=False)
    status: Mapped[BudgetLedgerStatus] = mapped_column(
        Enum(BudgetLedgerStatus, native_enum=False, create_constraint=True, values_callable=lambda values: [item.value for item in values], length=16),
        default=BudgetLedgerStatus.RESERVED,
        index=True,
        nullable=False,
    )
    reserved_fen: Mapped[int] = mapped_column(Integer, nullable=False)
    actual_fen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    released_fen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    project: Mapped[WeddingProject] = relationship(back_populates="budget_entries")
    job: Mapped[GenerationJob] = relationship(back_populates="budget_entry")


class WeddingAuditEvent(Base):
    __tablename__ = "wedding_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey("wedding_projects.id"), index=True, nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    action: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    project_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)

    project: Mapped[WeddingProject] = relationship(back_populates="audit_events")


class WeddingAccountUsage(Base):
    __tablename__ = 'wedding_account_usage'
    owner_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    reserved_fen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    spent_fen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class WeddingBillingReceipt(Base):
    """Minimal accounting deliberately has no cascading private project foreign keys."""
    __tablename__ = 'wedding_billing_receipts'
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    operation_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    project_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    job_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    task_id: Mapped[str | None] = mapped_column(String(160), index=True)
    request_id: Mapped[str | None] = mapped_column(String(160))
    state: Mapped[str] = mapped_column(String(24), default='reserved', nullable=False)
    reserved_fen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    estimated_fen: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    billed_fen: Mapped[int | None] = mapped_column(Integer)
    settled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    usage: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    price_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class WeddingCreationJob(Base):
    __tablename__ = 'wedding_creation_jobs'
    __table_args__ = (UniqueConstraint('project_id', 'idempotency_key', name='uq_wedding_creation_idempotency'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    project_id: Mapped[str] = mapped_column(ForeignKey('wedding_projects.id'), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    project_version: Mapped[int] = mapped_column(Integer, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    draft_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default='queued', index=True, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    completed_operations: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_operations: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    demo: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    result_story_id: Mapped[str | None] = mapped_column(String(36))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now, nullable=False)


class WeddingObjectCleanup(Base):
    """Private object ownership survives project/asset deletion and failed uploads."""
    __tablename__ = 'wedding_object_cleanup'
    object_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default='pending', index=True, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    discard_requested: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    cleanup_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
