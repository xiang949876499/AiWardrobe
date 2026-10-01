from __future__ import annotations

import base64
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.asset_access import asset_content_url, create_asset_token
from app.config import Settings, get_settings
from app.purchase_analysis import PURCHASE_RULE_VERSION
from app.candidate_ownership import link_owned_candidate, lock_ownership_user, save_owned_candidate
from app.database import get_db
from app.decision_context import bump_revision, get_decision_context
from app.models import (
    FeedbackEvent,
    AnalysisTask,
    Garment,
    Outfit,
    PurchaseAnalysisSnapshot,
    PurchaseCandidate,
    User,
    UserDecisionContext,
    UserPreference,
    WearEvent,
    WearEventItem,
    utc_now,
)
from app.security import get_current_user
from app.task_service import create_task, task_response

router = APIRouter(prefix="/v2", tags=["decision-history"])


class CandidatePatch(BaseModel):
    expected_revision: int = Field(ge=1)
    category: str | None = Field(default=None, pattern=r"^(top|bottom|outerwear|dress|shoes|bag|accessory)$")
    colors: list[str] | None = Field(default=None, max_length=20)
    style: str | None = Field(default=None, max_length=120)
    material: str | None = Field(default=None, max_length=120)
    season: list[str] | None = Field(default=None, max_length=4)
    fit: str | None = Field(default=None, max_length=120)
    tags: list[str] | None = Field(default=None, max_length=20)
    actual_price: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    currency: str | None = Field(default=None, min_length=3, max_length=3)

    @model_validator(mode="after")
    def price_uses_supported_currency(self):
        if self.actual_price is not None and (self.currency or "CNY") != "CNY":
            raise ValueError("unsupported_currency")
        return self


class CandidateDecisionPatch(BaseModel):
    expected_revision: int = Field(ge=1)
    decision_status: str


class CandidateSaveRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    confirm_owned: bool
    tags: list[str] | None = None


class CandidateLinkRequest(BaseModel):
    expected_revision: int = Field(ge=1)
    garment_id: str = Field(min_length=1, max_length=36)
    confirm_owned: bool


class PreferenceV2Update(BaseModel):
    primary_goal: str = Field(default="", max_length=120)
    scenes: list[str] = Field(default_factory=list, max_length=10)
    styles: list[str] = Field(default_factory=list, max_length=10)
    avoid_types: list[str] = Field(default_factory=list, max_length=10)
    budget_range: str = Field(default="", max_length=80)
    budget_min: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    budget_max: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=2)
    currency: str = "CNY"
    avoid_categories: list[str] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def validate_preferences(self):
        if self.currency != "CNY":
            raise ValueError("unsupported_currency")
        if self.budget_min is not None and self.budget_max is not None and self.budget_min > self.budget_max:
            raise ValueError("budget_min_exceeds_max")
        allowed = {"top", "bottom", "outerwear", "dress", "shoes", "bag", "accessory"}
        if set(self.avoid_categories) - allowed:
            raise ValueError("invalid_avoid_category")
        return self


class FeedbackCreate(BaseModel):
    target_type: str
    target_id: str = Field(min_length=1, max_length=36)
    snapshot_revision: int = Field(ge=1)
    value: str
    reason_code: str | None = None
    garment_ids: list[str] = Field(default_factory=list, max_length=20)
    occasion: str | None = Field(default=None, max_length=80)
    exclude_next_time: bool = False

    @model_validator(mode="after")
    def validate_feedback(self):
        if self.target_type not in {"candidate", "outfit"}:
            raise ValueError("invalid_feedback_target")
        if self.value not in {"helpful", "not_helpful"}:
            raise ValueError("invalid_feedback_value")
        allowed = {"style_mismatch", "weather_mismatch", "missing_item", "duplicate", "avoid_item", "other"}
        if self.reason_code is not None and self.reason_code not in allowed:
            raise ValueError("invalid_feedback_reason")
        if self.value == "helpful" and self.reason_code:
            raise ValueError("helpful_feedback_cannot_have_reason")
        if self.reason_code == "avoid_item" and (not self.garment_ids or not self.exclude_next_time):
            raise ValueError("avoid_item_requires_explicit_exclusion")
        if self.reason_code != "avoid_item" and self.exclude_next_time:
            raise ValueError("exclusion_requires_avoid_item")
        return self


class WearCreate(BaseModel):
    local_date: date
    timezone: str = Field(min_length=1, max_length=80)
    outfit_id: str | None = Field(default=None, max_length=36)
    garment_ids: list[str] = Field(default_factory=list, max_length=20)
    occasion: str | None = Field(default=None, max_length=80)

    @model_validator(mode="after")
    def validate_subject(self):
        if bool(self.outfit_id) == bool(self.garment_ids):
            raise ValueError("provide_exactly_one_outfit_or_garments")
        return self


@router.get("/purchase/candidates")
def list_candidates(
    decision_status: str | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    query = select(PurchaseCandidate).where(PurchaseCandidate.user_id == current_user.id)
    if decision_status:
        query = query.where(PurchaseCandidate.decision_status == decision_status)
    if cursor:
        created_at, candidate_id = _decode_cursor(cursor, current_user.id, decision_status)
        query = query.where(
            or_(PurchaseCandidate.created_at < created_at,
                and_(PurchaseCandidate.created_at == created_at, PurchaseCandidate.id < candidate_id))
        )
    rows = list(db.execute(query.order_by(PurchaseCandidate.created_at.desc(), PurchaseCandidate.id.desc()).limit(limit + 1)).scalars())
    has_more = len(rows) > limit
    items = rows[:limit]
    return {
        "items": [_candidate_summary(db, current_user.id, item) for item in items],
        "next_cursor": _encode_cursor(items[-1], current_user.id, decision_status) if has_more and items else None,
        "has_more": has_more,
    }


@router.get("/purchase/candidates/{candidate_id}")
def get_candidate(
    candidate_id: str,
    snapshot_revision: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    candidate = _owned_candidate(db, current_user.id, candidate_id)
    if snapshot_revision is None:
        snapshot = db.get(PurchaseAnalysisSnapshot, candidate.latest_snapshot_id) if candidate.latest_snapshot_id else None
    else:
        snapshot = db.execute(select(PurchaseAnalysisSnapshot).where(
            PurchaseAnalysisSnapshot.candidate_id == candidate.id,
            PurchaseAnalysisSnapshot.snapshot_revision == snapshot_revision,
        )).scalar_one_or_none()
    return _candidate_summary(db, current_user.id, candidate, snapshot=snapshot, include_history=True)


@router.patch("/purchase/candidates/{candidate_id}")
def patch_candidate(
    candidate_id: str,
    body: CandidatePatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    candidate = _owned_candidate(db, current_user.id, candidate_id, lock=True)
    _check_revision(candidate.revision, body.expected_revision)
    updates = body.model_dump(exclude_unset=True, exclude={"expected_revision"})
    changed = False
    for key, value in updates.items():
        if key in {"colors", "season", "tags"} and value is not None:
            value = _clean_values(value)
        if getattr(candidate, key) != value:
            changed = True
            setattr(candidate, key, value)
    if not changed:
        return _candidate_summary(db, current_user.id, candidate)
    if "actual_price" in updates:
        candidate.currency = (body.currency or "CNY") if body.actual_price is not None else None
    candidate.revision += 1
    candidate.updated_at = utc_now()
    db.commit()
    db.refresh(candidate)
    return _candidate_summary(db, current_user.id, candidate)


@router.patch("/purchase/candidates/{candidate_id}/decision")
def update_candidate_decision(
    candidate_id: str,
    body: CandidateDecisionPatch,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    if body.decision_status not in {"considering", "bought", "skipped", "returned"}:
        raise HTTPException(status_code=422, detail={"code": "invalid_decision_status"})
    candidate = _owned_candidate(db, current_user.id, candidate_id, lock=True)
    _check_revision(candidate.revision, body.expected_revision)
    if candidate.decision_status == body.decision_status:
        return _candidate_summary(db, current_user.id, candidate)
    transitions = {
        "considering": {"bought", "skipped"},
        "bought": {"returned"},
        "skipped": {"considering"},
        "returned": {"considering"},
    }
    if body.decision_status not in transitions.get(candidate.decision_status, set()):
        raise HTTPException(status_code=409, detail={"code": "invalid_decision_transition"})
    candidate.decision_status = body.decision_status
    candidate.revision += 1
    candidate.updated_at = utc_now()
    db.commit()
    db.refresh(candidate)
    return _candidate_summary(db, current_user.id, candidate)


@router.post("/purchase/candidates/{candidate_id}/save", status_code=201)
def save_candidate(
    candidate_id: str,
    body: CandidateSaveRequest,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    result = save_owned_candidate(db, current_user.id, candidate_id,
        expected_revision=body.expected_revision, confirm_owned=body.confirm_owned,
        tags=body.tags, idempotency_key=idempotency_key)
    response.status_code = status.HTTP_201_CREATED if result.created else status.HTTP_200_OK
    return result.payload


@router.post("/purchase/candidates/{candidate_id}/link-owned-garment")
def link_owned_garment(
    candidate_id: str,
    body: CandidateLinkRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return link_owned_candidate(db, current_user.id, candidate_id, body.garment_id,
        expected_revision=body.expected_revision, confirm_owned=body.confirm_owned,
        idempotency_key=idempotency_key).payload


@router.post("/purchase/candidates/{candidate_id}/reanalyze", status_code=202)
def reanalyze_candidate(
    candidate_id: str,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    key = _normalize_idempotency_key(idempotency_key)
    existing = db.execute(select(AnalysisTask).where(
        AnalysisTask.user_id == current_user.id,
        AnalysisTask.kind == "purchase_reanalysis",
        AnalysisTask.idempotency_key == key,
    )).scalar_one_or_none()
    if existing is not None:
        if str((existing.input_parameters or {}).get("candidate_id") or "") != candidate_id:
            raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"})
        return task_response(db, existing)
    candidate = _owned_candidate(db, current_user.id, candidate_id, lock=True)
    candidate.requested_snapshot_revision += 1
    candidate.analysis_status = "queued"
    requested_revision = candidate.requested_snapshot_revision
    task = create_task(
        db,
        current_user,
        "purchase_reanalysis",
        {
            "candidate_id": candidate.id,
            "candidate_revision": candidate.revision,
            "snapshot_revision": requested_revision,
        },
        key,
        input_refs={"candidate_id": candidate.id},
        queue_limit=settings.task_user_queue_limit,
    )
    return task_response(db, task)


@router.post("/garments/{garment_id}/confirm")
def confirm_garment(
    garment_id: str,
    body: dict[str, object],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    garment = db.execute(select(Garment).where(Garment.id == garment_id, Garment.user_id == current_user.id).with_for_update()).scalar_one_or_none()
    if garment is None:
        raise HTTPException(status_code=404, detail={"code": "garment_not_found"})
    tags = body.get("tags", garment.tags)
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise HTTPException(status_code=422, detail={"code": "invalid_garment_tags"})
    changed = garment.review_status != "confirmed" or garment.status != "ready" or _clean_values(tags) != (garment.tags or [])
    garment.tags = _clean_values(tags)
    garment.status = "ready"
    garment.review_status = "confirmed"
    garment.analysis_source = "manual" if garment.analysis_source == "legacy_unknown" else garment.analysis_source
    garment.user_confirmed_at = utc_now()
    if changed:
        bump_revision(db, current_user.id, "wardrobe_revision")
    db.commit()
    return {"id": garment.id, "status": garment.status, "review_status": garment.review_status, "tags": garment.tags}


@router.put("/preferences/me")
def update_preferences_v2(
    body: PreferenceV2Update,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    preference = db.execute(select(UserPreference).where(UserPreference.user_id == current_user.id).with_for_update()).scalar_one_or_none()
    if preference is None:
        preference = UserPreference(user_id=current_user.id)
        db.add(preference)
        db.flush()
    values = body.model_dump()
    for key in ("scenes", "styles", "avoid_types", "avoid_categories"):
        values[key] = _clean_values(values[key])
    changed = any(getattr(preference, key) != value for key, value in values.items())
    if changed:
        for key, value in values.items():
            setattr(preference, key, value)
        bump_revision(db, current_user.id, "preference_revision")
    db.commit()
    context = get_decision_context(db, current_user.id)
    return {**values, "revision": context.preference_revision}


@router.get("/preferences/me")
def get_preferences_v2(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    preference = db.execute(select(UserPreference).where(UserPreference.user_id == current_user.id)).scalar_one_or_none()
    context = get_decision_context(db, current_user.id)
    if preference is None:
        return {
            "primary_goal": "", "scenes": [], "styles": [], "avoid_types": [], "budget_range": "",
            "budget_min": None, "budget_max": None, "currency": "CNY", "avoid_categories": [],
            "revision": context.preference_revision,
        }
    return {
        "primary_goal": preference.primary_goal,
        "scenes": preference.scenes or [],
        "styles": preference.styles or [],
        "avoid_types": preference.avoid_types or [],
        "budget_range": preference.budget_range,
        "budget_min": str(preference.budget_min) if preference.budget_min is not None else None,
        "budget_max": str(preference.budget_max) if preference.budget_max is not None else None,
        "currency": preference.currency,
        "avoid_categories": preference.avoid_categories or [],
        "revision": context.preference_revision,
    }


@router.post("/feedback", status_code=201)
def create_feedback(
    body: FeedbackCreate,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    key = _normalize_idempotency_key(idempotency_key)
    old = db.execute(select(FeedbackEvent).where(
        FeedbackEvent.user_id == current_user.id,
        FeedbackEvent.idempotency_key == key,
    )).scalar_one_or_none()
    if old is not None:
        if _feedback_matches(old, body):
            return _feedback_payload(old)
        raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"})
    context, style_labels, occasion, temperature, target_item_ids = _feedback_target_context(db, current_user.id, body)
    if body.reason_code == "avoid_item":
        if not set(body.garment_ids).issubset(target_item_ids):
            raise HTTPException(status_code=422, detail={"code": "feedback_item_not_in_target"})
        owned_ids = set(db.execute(select(Garment.id).where(
            Garment.user_id == current_user.id,
            Garment.id.in_(body.garment_ids),
            Garment.status == "ready",
            Garment.review_status == "confirmed",
            Garment.user_confirmed_at.is_not(None),
        )).scalars())
        if owned_ids != set(body.garment_ids):
            raise HTTPException(status_code=404, detail={"code": "garment_not_found"})
    temperature_bucket = _temperature_bucket(temperature)
    event = FeedbackEvent(
        user_id=current_user.id,
        target_type=body.target_type,
        target_id=body.target_id,
        snapshot_revision=body.snapshot_revision,
        value=body.value,
        reason_code=body.reason_code,
        garment_ids=_clean_values(body.garment_ids),
        occasion=occasion,
        context={
            "style_labels": sorted(set(style_labels)),
            "occasion": occasion,
            "temperature_bucket": temperature_bucket,
            "rule_version": context.get("rule_version", "purchase-v1"),
        },
        idempotency_key=key,
    )
    db.add(event)
    bump_revision(db, current_user.id, "feedback_revision")
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(select(FeedbackEvent).where(
            FeedbackEvent.user_id == current_user.id,
            FeedbackEvent.idempotency_key == key,
        )).scalar_one_or_none()
        if existing is not None and _feedback_matches(existing, body):
            return _feedback_payload(existing)
        raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"})
    db.refresh(event)
    return _feedback_payload(event)


@router.delete("/feedback/{event_id}", status_code=204)
def revoke_feedback(
    event_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    event = db.execute(select(FeedbackEvent).where(FeedbackEvent.id == event_id, FeedbackEvent.user_id == current_user.id).with_for_update()).scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail={"code": "feedback_not_found"})
    if event.revoked_at is None:
        event.revoked_at = utc_now()
        bump_revision(db, current_user.id, "feedback_revision")
        db.commit()


@router.get("/feedback")
def list_feedback(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    query = select(FeedbackEvent).where(FeedbackEvent.user_id == current_user.id)
    if cursor:
        created_at, event_id = _decode_cursor(cursor, current_user.id, "feedback")
        query = query.where(or_(FeedbackEvent.created_at < created_at, and_(FeedbackEvent.created_at == created_at, FeedbackEvent.id < event_id)))
    rows = list(db.execute(query.order_by(FeedbackEvent.created_at.desc(), FeedbackEvent.id.desc()).limit(limit + 1)).scalars())
    has_more = len(rows) > limit
    items = rows[:limit]
    return {"items": [_feedback_payload(item) for item in items], "next_cursor": _encode_generic_cursor(items[-1], current_user.id, "feedback") if has_more and items else None, "has_more": has_more}


@router.post("/wear-events", status_code=201)
def create_wear_event(
    body: WearCreate,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    key = _normalize_idempotency_key(idempotency_key)
    existing = db.execute(select(WearEvent).where(WearEvent.user_id == current_user.id, WearEvent.idempotency_key == key)).scalar_one_or_none()
    if existing is not None:
        if not _wear_matches(db, existing, body):
            raise HTTPException(status_code=409, detail={"code": "idempotency_conflict"})
        return _wear_payload(db, existing)
    try:
        tz = ZoneInfo(body.timezone)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_timezone"}) from exc
    if body.local_date > datetime.now(tz).date():
        raise HTTPException(status_code=422, detail={"code": "wear_date_in_future"})
    if body.outfit_id:
        outfit = db.execute(select(Outfit).where(Outfit.id == body.outfit_id, Outfit.user_id == current_user.id)).scalar_one_or_none()
        if outfit is None:
            raise HTTPException(status_code=404, detail={"code": "outfit_not_found"})
        garment_ids = list(dict.fromkeys(str(item.get("garment_id")) for item in outfit.items or [] if item.get("garment_id")))
        occasion = body.occasion or outfit.occasion
        outfit_id = outfit.id
    else:
        garment_ids = _clean_values(body.garment_ids)
        occasion = body.occasion
        outfit_id = None
    garments = list(db.execute(select(Garment).where(
        Garment.user_id == current_user.id,
        Garment.id.in_(garment_ids),
        Garment.status == "ready",
        Garment.review_status == "confirmed",
        Garment.user_confirmed_at.is_not(None),
    )).scalars()) if garment_ids else []
    if {item.id for item in garments} != set(garment_ids):
        raise HTTPException(status_code=404, detail={"code": "garment_not_found"})
    event = WearEvent(
        user_id=current_user.id,
        local_date=body.local_date.isoformat(),
        timezone=body.timezone,
        outfit_id=outfit_id,
        occasion=occasion,
        idempotency_key=key,
    )
    db.add(event)
    db.flush()
    for garment in garments:
        db.add(WearEventItem(
            wear_event_id=event.id,
            garment_id=garment.id,
            garment_snapshot={"category": garment.category, "colors": garment.colors or [], "tags": garment.tags or [], "image_available": bool(garment.asset_id or garment.image_url)},
        ))
    decision_context = get_decision_context(db, current_user.id, lock=True)
    if decision_context.wear_tracking_started_at is None:
        decision_context.wear_tracking_started_at = utc_now()
        decision_context.wear_tracking_timezone = body.timezone
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        event = db.execute(select(WearEvent).where(WearEvent.user_id == current_user.id, WearEvent.idempotency_key == key)).scalar_one_or_none()
        if event is not None:
            return _wear_payload(db, event)
        raise HTTPException(status_code=409, detail={"code": "wear_event_conflict"})
    db.refresh(event)
    return _wear_payload(db, event)


@router.get("/wear-events")
def list_wear_events(
    start: date | None = None,
    end: date | None = None,
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    query = select(WearEvent).where(WearEvent.user_id == current_user.id)
    if start:
        query = query.where(WearEvent.local_date >= start.isoformat())
    if end:
        query = query.where(WearEvent.local_date <= end.isoformat())
    if cursor:
        created_at, event_id = _decode_cursor(cursor, current_user.id, f"wear:{start}:{end}")
        query = query.where(or_(WearEvent.created_at < created_at, and_(WearEvent.created_at == created_at, WearEvent.id < event_id)))
    rows = list(db.execute(query.order_by(WearEvent.created_at.desc(), WearEvent.id.desc()).limit(limit + 1)).scalars())
    has_more = len(rows) > limit
    items = rows[:limit]
    return {"items": [_wear_payload(db, item) for item in items], "next_cursor": _encode_generic_cursor(items[-1], current_user.id, f"wear:{start}:{end}") if has_more and items else None, "has_more": has_more}


@router.delete("/wear-events/{event_id}", status_code=204)
def revoke_wear_event(
    event_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    event = db.execute(select(WearEvent).where(WearEvent.id == event_id, WearEvent.user_id == current_user.id).with_for_update()).scalar_one_or_none()
    if event is None:
        raise HTTPException(status_code=404, detail={"code": "wear_event_not_found"})
    if event.revoked_at is None:
        event.revoked_at = utc_now()
        db.commit()


def _owned_candidate(db: Session, user_id: str, candidate_id: str, *, lock: bool = False) -> PurchaseCandidate:
    query = select(PurchaseCandidate).where(PurchaseCandidate.id == candidate_id, PurchaseCandidate.user_id == user_id)
    if lock:
        # Ownership writes and create_task use this account lock first. Keep the
        # candidate lock through snapshot allocation/task creation in that order.
        lock_ownership_user(db, user_id)
        query = query.with_for_update()
    candidate = db.execute(query).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(status_code=404, detail={"code": "candidate_not_found"})
    return candidate


def _check_revision(actual: int, expected: int) -> None:
    if actual != expected:
        raise HTTPException(status_code=409, detail={"code": "revision_conflict", "current_revision": actual})


def _candidate_summary(
    db: Session,
    user_id: str,
    candidate: PurchaseCandidate,
    *,
    snapshot: PurchaseAnalysisSnapshot | None = None,
    include_history: bool = False,
) -> dict[str, object]:
    if snapshot is None and candidate.latest_snapshot_id:
        snapshot = db.get(PurchaseAnalysisSnapshot, candidate.latest_snapshot_id)
    context = get_decision_context(db, user_id)
    stale = snapshot is None or any((
        snapshot.rule_version != PURCHASE_RULE_VERSION,
        snapshot.wardrobe_revision != context.wardrobe_revision,
        snapshot.preference_revision != context.preference_revision,
        snapshot.feedback_revision != context.feedback_revision,
        snapshot.snapshot_revision < candidate.requested_snapshot_revision,
        int((snapshot.decision or {}).get("revision", -1)) != candidate.revision,
    ))
    access_url = None
    if candidate.asset_id:
        token, _ = create_asset_token(get_settings().jwt_secret, candidate.asset_id, user_id, "thumbnail")
        access_url = f"/v2/assets/{candidate.asset_id}/content?variant=thumbnail&asset_token={token}"
    decision = dict(snapshot.decision or {}) if snapshot else {}
    similar_source = candidate.similar_items or []
    similar_ids = [str(item.get("garment_id") or "") for item in similar_source]
    similar_garments = {
        item.id: item
        for item in db.execute(select(Garment).where(
            Garment.user_id == user_id,
            Garment.id.in_(similar_ids),
        )).scalars()
    } if similar_ids else {}
    similar_items = [
        {
            **item,
            "image_url": asset_content_url(get_settings().jwt_secret, similar_garments[str(item.get("garment_id"))].asset_id, user_id, "thumbnail")
            if str(item.get("garment_id")) in similar_garments and similar_garments[str(item.get("garment_id"))].asset_id
            else "",
        }
        for item in similar_source
    ]
    analysis_payload = decision.get("analysis") if isinstance(decision.get("analysis"), dict) else candidate.analysis or {}
    from app.asset_service import purchase_outfit_ideas_response

    decision["analysis"] = {**analysis_payload, "similar_items": similar_items,
                            "outfit_ideas": purchase_outfit_ideas_response(db, candidate, analysis_payload, get_settings())}
    result: dict[str, object] = {
        "id": candidate.id,
        "title": candidate.title,
        "product_url": candidate.product_url,
        "domain": candidate.domain,
        "asset_id": candidate.asset_id,
        "image_url": access_url,
        "category": candidate.category,
        "colors": candidate.colors or [],
        "style": candidate.style,
        "material": candidate.material,
        "season": candidate.season or [],
        "fit": candidate.fit,
        "tags": candidate.tags or [],
        "analysis_source": candidate.analysis_source,
        "actual_price": str(candidate.actual_price) if candidate.actual_price is not None else None,
        "currency": candidate.currency,
        "recommendation": decision.get("recommendation", candidate.recommendation),
        "score": decision.get("score", candidate.decision_score),
        "reason_summary": decision.get("reason_summary", candidate.reason_summary),
        "analysis": decision.get("analysis", candidate.analysis or {}),
        "similar_items": similar_items,
        "coverage": snapshot.coverage if snapshot else (candidate.analysis or {}).get("coverage", {}),
        "evidence": snapshot.evidence if snapshot else (candidate.analysis or {}).get("evidence", {}),
        "decision_status": candidate.decision_status,
        "analysis_status": candidate.analysis_status,
        "ownership_state": candidate.ownership_state,
        "owned_garment_id": candidate.owned_garment_id,
        "revision": candidate.revision,
        "snapshot_revision": snapshot.snapshot_revision if snapshot else None,
        "stale": stale,
        "created_at": candidate.created_at,
        "updated_at": candidate.updated_at,
    }
    if include_history:
        def history_decision(snapshot_item):
            stored = dict(snapshot_item.decision or {})
            stored_analysis = stored.get("analysis") if isinstance(stored.get("analysis"), dict) else {}
            return {**stored, "analysis": {**stored_analysis, "outfit_ideas": purchase_outfit_ideas_response(
                db, candidate, stored_analysis, get_settings(),
            )}}

        result["snapshots"] = [
            {"snapshot_revision": item.snapshot_revision, "generated_at": item.generated_at, "stale": any((
                item.rule_version != PURCHASE_RULE_VERSION,
                item.wardrobe_revision != context.wardrobe_revision,
                item.preference_revision != context.preference_revision,
                item.feedback_revision != context.feedback_revision,
                int((item.decision or {}).get("revision", -1)) != candidate.revision,
            )), "decision": history_decision(item), "coverage": item.coverage, "evidence": item.evidence}
            for item in db.execute(select(PurchaseAnalysisSnapshot).where(
                PurchaseAnalysisSnapshot.candidate_id == candidate.id,
            ).order_by(PurchaseAnalysisSnapshot.snapshot_revision.desc())).scalars()
        ]
    return result


def _encode_cursor(row: PurchaseCandidate, user_id: str, filter_value: str | None) -> str:
    return _encode_payload({"user": user_id, "filter": filter_value, "created_at": row.created_at.isoformat(), "id": row.id})


def _encode_generic_cursor(row, user_id: str, filter_value: str) -> str:
    return _encode_payload({"user": user_id, "filter": filter_value, "created_at": row.created_at.isoformat(), "id": row.id})


def _decode_cursor(cursor: str, user_id: str, filter_value: str | None) -> tuple[datetime, str]:
    value = _decode_payload(cursor)
    if value.get("user") != user_id or value.get("filter") != filter_value:
        raise HTTPException(status_code=422, detail={"code": "invalid_cursor"})
    try:
        return datetime.fromisoformat(value["created_at"]), str(UUID(value["id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_cursor"}) from exc


def _encode_payload(value: dict[str, object]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_payload(value: str) -> dict[str, object]:
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        parsed = json.loads(decoded)
        if not isinstance(parsed, dict):
            raise ValueError
        return parsed
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_cursor"}) from exc


def _normalize_idempotency_key(value: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_idempotency_key"}) from exc


def _clean_values(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))[:20]


def _feedback_target_context(db: Session, user_id: str, body: FeedbackCreate) -> tuple[dict[str, object], list[str], str | None, int | None, set[str]]:
    if body.target_type == "candidate":
        candidate = _owned_candidate(db, user_id, body.target_id)
        snapshot = db.execute(select(PurchaseAnalysisSnapshot).where(
            PurchaseAnalysisSnapshot.candidate_id == candidate.id,
            PurchaseAnalysisSnapshot.snapshot_revision == body.snapshot_revision,
        )).scalar_one_or_none()
        if snapshot is None:
            raise HTTPException(status_code=409, detail={"code": "snapshot_not_found"})
        target_item_ids = {
            str(item.get("garment_id"))
            for item in candidate.similar_items or []
            if item.get("garment_id")
        }
        return {"rule_version": snapshot.rule_version}, [candidate.style, *(candidate.tags or [])], body.occasion, None, target_item_ids
    outfit = db.execute(select(Outfit).where(Outfit.id == body.target_id, Outfit.user_id == user_id)).scalar_one_or_none()
    if outfit is None:
        raise HTTPException(status_code=404, detail={"code": "outfit_not_found"})
    if outfit.result_revision != body.snapshot_revision:
        raise HTTPException(status_code=409, detail={"code": "snapshot_not_found"})
    garment_ids = [str(item.get("garment_id")) for item in outfit.items or [] if item.get("garment_id")]
    garments = list(db.execute(select(Garment).where(Garment.user_id == user_id, Garment.id.in_(garment_ids))).scalars()) if garment_ids else []
    labels = [label for garment in garments for label in [garment.style, *(garment.tags or [])] if label]
    return {"rule_version": "outfit-v1"}, labels, outfit.occasion, outfit.temperature, set(garment_ids)


def _temperature_bucket(value: int | None) -> str | None:
    if value is None:
        return None
    if value < 10:
        return "cold"
    if value < 20:
        return "mild"
    if value < 30:
        return "warm"
    return "hot"


def _feedback_matches(event: FeedbackEvent, body: FeedbackCreate) -> bool:
    return all((
        event.target_type == body.target_type,
        event.target_id == body.target_id,
        event.snapshot_revision == body.snapshot_revision,
        event.value == body.value,
        event.reason_code == body.reason_code,
        event.garment_ids == _clean_values(body.garment_ids),
        body.occasion is None or event.occasion == body.occasion,
    ))


def _feedback_payload(event: FeedbackEvent) -> dict[str, object]:
    return {"id": event.id, "target_type": event.target_type, "target_id": event.target_id, "snapshot_revision": event.snapshot_revision, "value": event.value, "reason_code": event.reason_code, "garment_ids": event.garment_ids or [], "context": event.context or {}, "revoked_at": event.revoked_at, "created_at": event.created_at}


def _wear_payload(db: Session, event: WearEvent) -> dict[str, object]:
    items = list(db.execute(select(WearEventItem).where(WearEventItem.wear_event_id == event.id).order_by(WearEventItem.garment_id)).scalars())
    return {"id": event.id, "local_date": event.local_date, "timezone": event.timezone, "outfit_id": event.outfit_id, "occasion": event.occasion, "items": [{"garment_id": item.garment_id, "snapshot": item.garment_snapshot} for item in items], "revoked_at": event.revoked_at, "created_at": event.created_at}


def _wear_matches(db: Session, event: WearEvent, body: WearCreate) -> bool:
    item_ids = set(db.execute(select(WearEventItem.garment_id).where(WearEventItem.wear_event_id == event.id)).scalars())
    requested_ids = set(body.garment_ids)
    if body.outfit_id:
        outfit = db.execute(select(Outfit).where(Outfit.id == body.outfit_id, Outfit.user_id == event.user_id)).scalar_one_or_none()
        requested_ids = {
            str(item.get("garment_id"))
            for item in (outfit.items if outfit else [])
            if item.get("garment_id")
        }
    return all((
        event.local_date == body.local_date.isoformat(),
        event.timezone == body.timezone,
        event.outfit_id == body.outfit_id,
        body.occasion is None or event.occasion == body.occasion,
        item_ids == requested_ids,
    ))
