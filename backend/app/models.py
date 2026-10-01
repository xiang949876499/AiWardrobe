from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, text
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
    budget_min: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    budget_max: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="CNY", server_default="CNY")
    avoid_categories: Mapped[list[str]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
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
    review_status: Mapped[str] = mapped_column(String(32), index=True, default="pending_review")
    analysis_source: Mapped[str] = mapped_column(
        String(32), default="legacy_unknown", server_default="legacy_unknown", nullable=False
    )
    provider: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    user_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_candidate_id: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
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
    decision_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason_summary: Mapped[str] = mapped_column(Text, default="")
    analysis: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), index=True, default="ready")
    analysis_status: Mapped[str] = mapped_column(
        String(32), default="ready", server_default="ready", index=True, nullable=False
    )
    decision_status: Mapped[str] = mapped_column(
        String(32), default="considering", server_default="considering", index=True, nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    requested_snapshot_revision: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    latest_snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    analysis_source: Mapped[str] = mapped_column(
        String(32), default="legacy_unknown", server_default="legacy_unknown", nullable=False
    )
    actual_price: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    owned_garment_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    ownership_state: Mapped[str] = mapped_column(
        String(32), default="never_saved", server_default="never_saved", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user: Mapped[User] = relationship(back_populates="purchase_candidates")


class CandidateOwnershipOperation(Base):
    __tablename__ = "candidate_ownership_operations"
    __table_args__ = (
        UniqueConstraint("user_id", "operation", "idempotency_key", name="uq_candidate_ownership_replay"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False)
    input_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    response: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


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


class RateLimitCounter(Base):
    __tablename__ = "rate_limit_counters"

    scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    key: Mapped[str] = mapped_column(String(160), primary_key=True)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reset_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProviderCapacitySlot(Base):
    __tablename__ = "provider_capacity_slots"
    __table_args__ = (
        UniqueConstraint("provider", "slot_index", name="uq_provider_slot_index"),
        UniqueConstraint("operation_id", name="uq_provider_slot_operation"),
    )

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    provider: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    slot_index: Mapped[int] = mapped_column(Integer, nullable=False)
    operation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ProviderSlotReleaseAudit(Base):
    __tablename__ = "provider_slot_release_audits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slot_id: Mapped[str] = mapped_column(ForeignKey("provider_capacity_slots.id"), nullable=False, index=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("provider_operations.id"), nullable=False, index=True)
    operator: Mapped[str] = mapped_column(String(120), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    released_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)


class UserDecisionContext(Base):
    __tablename__ = "user_decision_contexts"

    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    wardrobe_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    preference_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    feedback_revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    wear_tracking_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    wear_tracking_timezone: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class PurchaseAnalysisSnapshot(Base):
    __tablename__ = "purchase_analysis_snapshots"
    __table_args__ = (
        UniqueConstraint("candidate_id", "snapshot_revision", name="uq_purchase_snapshot_revision"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("purchase_candidates.id"), index=True, nullable=False)
    snapshot_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    wardrobe_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    preference_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    feedback_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(40), nullable=False)
    coverage: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    decision: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class FeedbackEvent(Base):
    __tablename__ = "feedback_events"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_feedback_user_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    target_type: Mapped[str] = mapped_column(String(24), nullable=False)
    target_id: Mapped[str] = mapped_column(String(36), nullable=False)
    snapshot_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[str] = mapped_column(String(24), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    garment_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    occasion: Mapped[str | None] = mapped_column(String(80), nullable=True)
    context: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WearEvent(Base):
    __tablename__ = "wear_events"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_wear_event_user_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    local_date: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    timezone: Mapped[str] = mapped_column(String(80), nullable=False)
    outfit_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    occasion: Mapped[str | None] = mapped_column(String(80), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class WearEventItem(Base):
    __tablename__ = "wear_event_items"
    __table_args__ = (
        UniqueConstraint("wear_event_id", "garment_id", name="uq_wear_event_garment"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    wear_event_id: Mapped[str] = mapped_column(ForeignKey("wear_events.id"), index=True, nullable=False)
    garment_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    garment_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    original_image_url: Mapped[str] = mapped_column(Text, nullable=False)
    original_image_key: Mapped[str] = mapped_column(String(512), nullable=False)
    asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
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
    result_revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
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


class StoredAsset(Base):
    __tablename__ = "stored_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(80), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    thumbnail_asset_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    unreferenced_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "idempotency_key", name="uq_analysis_task_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    kind: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="queued", server_default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="validating", server_default="validating")
    stage_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=True)
    stage_durations_ms: Mapped[dict[str, int] | None] = mapped_column(JSON, default=dict, nullable=True)
    input_refs: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    input_parameters: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(36), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    result_refs: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)


class ProviderOperation(Base):
    __tablename__ = "provider_operations"
    __table_args__ = (
        UniqueConstraint("task_id", "operation_key", "generation", name="uq_provider_operation_generation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("analysis_tasks.id"), index=True, nullable=False)
    operation_key: Mapped[str] = mapped_column(String(80), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    submission_status: Mapped[str] = mapped_column(
        String(24), default="not_submitted", server_default="not_submitted", index=True
    )
    provider_task_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    request_parameters: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    result_refs: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    result_data: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    quota_reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    slot_reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    slot_released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    slot_release_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reconciliation_evidence: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)
