from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import (
    BudgetLedger,
    BudgetLedgerStatus,
    ConsentInvite,
    GenerationJob,
    ParticipantReview,
    ParticipantReviewStatus,
    RenderVersion,
    RenderVersionStatus,
    StoryVersion,
    StoryVersionStatus,
    StoryboardShot,
    StoryboardShotStatus,
    WeddingAsset,
    WeddingConsentStatus,
    WeddingConsent,
    WeddingInviteStatus,
    WeddingJobStatus,
    WeddingParticipant,
    WeddingProject,
    WeddingProjectStatus,
    User,
    utc_now,
)

participant_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class WeddingActor:
    kind: Literal["owner", "participant"]
    actor_id: str
    owner_id: str | None = None
    participant_id: str | None = None
    project_id: str | None = None
    person_key: str | None = None


def _as_utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _set_status(entity: object, attribute: str, value: StrEnum, allowed: dict[StrEnum, set[StrEnum]]) -> None:
    current = type(value)(getattr(entity, attribute))
    if current == value:
        return
    if value not in allowed[current]:
        raise ValueError(f"Illegal {type(value).__name__} transition: {current.value} -> {value.value}")
    setattr(entity, attribute, value)


def set_consent_status(participant: WeddingParticipant, value: WeddingConsentStatus) -> None:
    _set_status(
        participant,
        "consent_status",
        WeddingConsentStatus(value),
        {
            WeddingConsentStatus.PENDING: {WeddingConsentStatus.ACTIVE, WeddingConsentStatus.DECLINED, WeddingConsentStatus.REVOKED},
            WeddingConsentStatus.ACTIVE: {WeddingConsentStatus.REVOKED},
            WeddingConsentStatus.DECLINED: {WeddingConsentStatus.PENDING, WeddingConsentStatus.ACTIVE},
            WeddingConsentStatus.REVOKED: {WeddingConsentStatus.PENDING, WeddingConsentStatus.ACTIVE},
        },
    )


def set_invite_status(invite: ConsentInvite, value: WeddingInviteStatus) -> None:
    _set_status(
        invite,
        "status",
        WeddingInviteStatus(value),
        {
            WeddingInviteStatus.ACTIVE: {WeddingInviteStatus.CONSUMED, WeddingInviteStatus.REVOKED, WeddingInviteStatus.EXPIRED},
            WeddingInviteStatus.CONSUMED: set(),
            WeddingInviteStatus.REVOKED: set(),
            WeddingInviteStatus.EXPIRED: set(),
        },
    )


def set_story_status(story: StoryVersion, value: StoryVersionStatus) -> None:
    _set_status(
        story,
        "status",
        StoryVersionStatus(value),
        {
            StoryVersionStatus.DRAFT: {StoryVersionStatus.APPROVED, StoryVersionStatus.SUPERSEDED},
            StoryVersionStatus.APPROVED: {StoryVersionStatus.SUPERSEDED},
            StoryVersionStatus.SUPERSEDED: set(),
        },
    )


def set_shot_status(shot: StoryboardShot, value: StoryboardShotStatus) -> None:
    _set_status(
        shot,
        "status",
        StoryboardShotStatus(value),
        {
            StoryboardShotStatus.DRAFT: {StoryboardShotStatus.FRAME_PENDING, StoryboardShotStatus.FAILED},
            StoryboardShotStatus.FRAME_PENDING: {StoryboardShotStatus.FRAME_APPROVED, StoryboardShotStatus.FAILED},
            StoryboardShotStatus.FRAME_APPROVED: {StoryboardShotStatus.GENERATING, StoryboardShotStatus.CANDIDATE_REVIEW, StoryboardShotStatus.FALLBACK, StoryboardShotStatus.FAILED, StoryboardShotStatus.FRAME_PENDING},
            StoryboardShotStatus.GENERATING: {StoryboardShotStatus.CANDIDATE_REVIEW, StoryboardShotStatus.FAILED, StoryboardShotStatus.FRAME_PENDING},
            StoryboardShotStatus.CANDIDATE_REVIEW: {StoryboardShotStatus.APPROVED, StoryboardShotStatus.FALLBACK, StoryboardShotStatus.GENERATING, StoryboardShotStatus.FAILED, StoryboardShotStatus.FRAME_PENDING},
            StoryboardShotStatus.APPROVED: {StoryboardShotStatus.CANDIDATE_REVIEW, StoryboardShotStatus.FALLBACK, StoryboardShotStatus.FRAME_PENDING},
            StoryboardShotStatus.FALLBACK: {StoryboardShotStatus.CANDIDATE_REVIEW, StoryboardShotStatus.APPROVED, StoryboardShotStatus.FRAME_PENDING},
            StoryboardShotStatus.FAILED: {StoryboardShotStatus.FRAME_PENDING, StoryboardShotStatus.GENERATING},
        },
    )


def set_review_status(review: ParticipantReview, value: ParticipantReviewStatus) -> None:
    normalized = ParticipantReviewStatus(value)
    if normalized not in {ParticipantReviewStatus.APPROVED, ParticipantReviewStatus.REJECTED}:
        raise ValueError(f"Invalid participant review status: {normalized}")
    review.result = normalized


def set_render_status(render: RenderVersion, value: RenderVersionStatus) -> None:
    _set_status(
        render,
        "status",
        RenderVersionStatus(value),
        {
            RenderVersionStatus.QUEUED: {RenderVersionStatus.PROCESSING, RenderVersionStatus.READY, RenderVersionStatus.FAILED, RenderVersionStatus.CANCELLED},
            RenderVersionStatus.PROCESSING: {RenderVersionStatus.READY, RenderVersionStatus.FAILED, RenderVersionStatus.CANCELLED},
            RenderVersionStatus.READY: {RenderVersionStatus.APPROVED, RenderVersionStatus.CANCELLED},
            RenderVersionStatus.APPROVED: {RenderVersionStatus.CANCELLED},
            RenderVersionStatus.FAILED: set(),
            RenderVersionStatus.CANCELLED: set(),
        },
    )


def set_budget_status(entry: BudgetLedger, value: BudgetLedgerStatus) -> None:
    _set_status(
        entry,
        "status",
        BudgetLedgerStatus(value),
        {
            BudgetLedgerStatus.RESERVED: {BudgetLedgerStatus.SETTLED, BudgetLedgerStatus.RELEASED},
            BudgetLedgerStatus.SETTLED: set(),
            BudgetLedgerStatus.RELEASED: set(),
        },
    )


def get_wedding_actor(
    credentials: HTTPAuthorizationCredentials | None = Depends(participant_bearer),
    db: Session = Depends(get_db),
) -> WeddingActor:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    settings = get_settings()
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token") from exc

    participant_id = payload.get("participant_id")
    if payload.get("token_type") == "wedding_participant":
        participant = db.get(WeddingParticipant, participant_id)
        now = utc_now()
        expires_at = _as_utc(participant.session_expires_at) if participant is not None else None
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        if (
            participant is None
            or participant.project_id != payload.get("project_id")
            or participant.consent_status != "active"
            or participant.session_token_hash is None
            or not hmac.compare_digest(participant.session_token_hash, token_hash)
            or expires_at is None
            or expires_at <= now
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Participant session is no longer active")
        return WeddingActor(
            kind="participant",
            actor_id=participant.id,
            participant_id=participant.id,
            project_id=participant.project_id,
            person_key=participant.person_key,
        )

    user_id = payload.get("sub")
    if not isinstance(user_id, str):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")
    from app.models import User

    if db.get(User, user_id) is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")
    return WeddingActor(kind="owner", actor_id=user_id, owner_id=user_id)


def require_project_owner(db: Session, actor: WeddingActor, project_id: str) -> WeddingProject:
    project = db.get(WeddingProject, project_id)
    if project is None or actor.kind != "owner" or actor.owner_id != project.owner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wedding project not found")
    return project


def require_participant_project(actor: WeddingActor, project_id: str) -> WeddingParticipant:
    if actor.kind != "participant" or actor.project_id != project_id or actor.participant_id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Wedding resource not found")
    return WeddingParticipant(
        id=actor.participant_id,
        project_id=project_id,
        person_key=actor.person_key or "",
        consent_status="active",
    )


def set_project_status(project: WeddingProject, new_status: WeddingProjectStatus) -> None:
    current = WeddingProjectStatus(project.status)
    allowed: dict[WeddingProjectStatus, set[WeddingProjectStatus]] = {
        WeddingProjectStatus.DRAFT: {WeddingProjectStatus.AWAITING_CONSENT, WeddingProjectStatus.STORY_REVIEW, WeddingProjectStatus.CANCELLED},
        WeddingProjectStatus.AWAITING_CONSENT: {WeddingProjectStatus.STORY_REVIEW, WeddingProjectStatus.DRAFT, WeddingProjectStatus.CANCELLED},
        WeddingProjectStatus.STORY_REVIEW: {WeddingProjectStatus.FRAME_REVIEW, WeddingProjectStatus.AWAITING_CONSENT, WeddingProjectStatus.CANCELLED},
        WeddingProjectStatus.FRAME_REVIEW: {WeddingProjectStatus.READY_TO_GENERATE, WeddingProjectStatus.STORY_REVIEW, WeddingProjectStatus.AWAITING_CONSENT, WeddingProjectStatus.CANCELLED},
        WeddingProjectStatus.READY_TO_GENERATE: {WeddingProjectStatus.GENERATING, WeddingProjectStatus.FRAME_REVIEW, WeddingProjectStatus.STORY_REVIEW, WeddingProjectStatus.AWAITING_CONSENT, WeddingProjectStatus.CANCELLED},
        WeddingProjectStatus.GENERATING: {WeddingProjectStatus.PREVIEW_REVIEW, WeddingProjectStatus.READY_TO_GENERATE, WeddingProjectStatus.STORY_REVIEW, WeddingProjectStatus.BLOCKED, WeddingProjectStatus.CANCELLED, WeddingProjectStatus.AWAITING_CONSENT},
        WeddingProjectStatus.PREVIEW_REVIEW: {WeddingProjectStatus.GENERATING, WeddingProjectStatus.READY_TO_EXPORT, WeddingProjectStatus.STORY_REVIEW, WeddingProjectStatus.AWAITING_CONSENT, WeddingProjectStatus.CANCELLED},
        WeddingProjectStatus.READY_TO_EXPORT: {WeddingProjectStatus.DELIVERED, WeddingProjectStatus.PREVIEW_REVIEW, WeddingProjectStatus.STORY_REVIEW, WeddingProjectStatus.AWAITING_CONSENT, WeddingProjectStatus.CANCELLED},
        WeddingProjectStatus.DELIVERED: {WeddingProjectStatus.PREVIEW_REVIEW, WeddingProjectStatus.STORY_REVIEW, WeddingProjectStatus.AWAITING_CONSENT, WeddingProjectStatus.CANCELLED},
        WeddingProjectStatus.BLOCKED: {WeddingProjectStatus.GENERATING, WeddingProjectStatus.READY_TO_GENERATE, WeddingProjectStatus.STORY_REVIEW, WeddingProjectStatus.AWAITING_CONSENT, WeddingProjectStatus.CANCELLED},
        WeddingProjectStatus.CANCELLED: {WeddingProjectStatus.DRAFT, WeddingProjectStatus.STORY_REVIEW, WeddingProjectStatus.AWAITING_CONSENT},
    }
    if new_status == current:
        return
    if new_status not in allowed[current]:
        raise ValueError(f"Illegal wedding project status transition: {current.value} -> {new_status.value}")
    project.status = new_status


def set_job_status(job: GenerationJob, new_status: WeddingJobStatus) -> None:
    current = WeddingJobStatus(job.status)
    allowed: dict[WeddingJobStatus, set[WeddingJobStatus]] = {
        WeddingJobStatus.QUEUED: {WeddingJobStatus.PROCESSING, WeddingJobStatus.CANCELLED, WeddingJobStatus.FAILED, WeddingJobStatus.NEEDS_ACTION},
        WeddingJobStatus.PROCESSING: {WeddingJobStatus.NEEDS_ACTION, WeddingJobStatus.COMPLETED, WeddingJobStatus.FAILED, WeddingJobStatus.CANCEL_REQUESTED, WeddingJobStatus.CANCELLED},
        WeddingJobStatus.NEEDS_ACTION: {WeddingJobStatus.QUEUED, WeddingJobStatus.CANCELLED, WeddingJobStatus.FAILED},
        WeddingJobStatus.CANCEL_REQUESTED: {WeddingJobStatus.CANCELLED, WeddingJobStatus.COMPLETED, WeddingJobStatus.FAILED},
        WeddingJobStatus.COMPLETED: set(),
        WeddingJobStatus.FAILED: set(),
        WeddingJobStatus.CANCELLED: set(),
    }
    if new_status == current:
        return
    if new_status not in allowed[current]:
        raise ValueError(f"Illegal generation job status transition: {current.value} -> {new_status.value}")
    job.status = new_status


def validate_active_consents(db: Session, project: WeddingProject) -> list[WeddingParticipant]:
    participants = list(
        db.execute(select(WeddingParticipant).where(WeddingParticipant.project_id == project.id)).scalars()
    )
    by_key = {participant.person_key: participant for participant in participants}
    for key in ("A", "B"):
        participant = by_key.get(key)
        if (
            participant is None
            or participant.consent_status != "active"
            or "portrait_processing" not in (participant.consent_scope or [])
            or not participant.provider_list
            or participant.confirmed_at is None
            or participant.revoked_at is not None
        ):
            raise HTTPException(status_code=409, detail="CONSENT_REQUIRED")
    return [by_key["A"], by_key["B"]]


def validate_identity_reviews(db: Session, project: WeddingProject, story: StoryVersion) -> None:
    shots = list(db.execute(select(StoryboardShot).where(StoryboardShot.story_version_id == story.id)).scalars())
    participants = {
        participant.person_key: participant
        for participant in db.execute(
            select(WeddingParticipant).where(
                WeddingParticipant.project_id == project.id,
                WeddingParticipant.consent_status == "active",
            )
        ).scalars()
    }
    for shot in shots:
        if shot.frame_asset_id is None:
            raise HTTPException(status_code=409, detail="STATIC_FRAME_APPROVAL_REQUIRED")
        for person_key in shot.person_keys:
            participant = participants.get(person_key)
            if participant is None:
                raise HTTPException(status_code=409, detail="IDENTITY_REVIEW_REQUIRED")
            review = db.execute(
                select(ParticipantReview).where(
                    ParticipantReview.project_id == project.id,
                    ParticipantReview.participant_id == participant.id,
                    ParticipantReview.project_version == project.current_version,
                    ParticipantReview.shot_id == shot.id,
                    ParticipantReview.asset_id == shot.frame_asset_id,
                    ParticipantReview.result == "approved",
                )
            ).scalar_one_or_none()
            if review is None:
                raise HTTPException(status_code=409, detail="IDENTITY_REVIEW_REQUIRED")


def reserve_generation_budget(
    db: Session,
    project: WeddingProject,
    job: GenerationJob,
    reservation_key: str,
    amount_fen: int,
) -> BudgetLedger:
    """Reserve a quote ceiling atomically; the worker settles or releases it later."""
    existing = db.execute(
        select(BudgetLedger).where(
            BudgetLedger.project_id == project.id,
            BudgetLedger.reservation_key == reservation_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.generation_job_id != job.id:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_KEY_REUSED")
        return existing
    if amount_fen <= 0:
        raise HTTPException(status_code=409, detail="BUDGET_RESERVATION_REQUIRED")
    # Serialize reservations for one owner across all projects. The row lock is
    # held by the caller's transaction until its commit, so a competing reserve
    # observes the first reservation before calculating its own headroom.
    owner_id = db.execute(
        select(User.id).where(User.id == project.owner_id).with_for_update()
    ).scalar_one_or_none()
    if owner_id is None:
        raise HTTPException(status_code=409, detail="BUDGET_OWNER_NOT_FOUND")
    if db.get_bind().dialect.name == "sqlite":
        # SQLite omits FOR UPDATE. A no-op owner-row update acquires its coarse
        # writer lock before the aggregate read, mirroring the PostgreSQL row lock.
        db.execute(
            update(User)
            .where(User.id == owner_id)
            .values(updated_at=User.updated_at)
            .execution_options(synchronize_session=False)
        )
    account_reserved_and_spent = db.scalar(
        select(
            func.coalesce(
                func.sum(WeddingProject.budget_spent_fen + WeddingProject.budget_reserved_fen),
                0,
            )
        ).where(WeddingProject.owner_id == owner_id)
    )
    if int(account_reserved_and_spent or 0) + amount_fen > get_settings().wedding_account_budget_limit_fen:
        raise HTTPException(status_code=409, detail="ACCOUNT_BUDGET_LIMIT_EXCEEDED")
    result = db.execute(
        update(WeddingProject)
        .where(
            WeddingProject.id == project.id,
            WeddingProject.budget_spent_fen
            + WeddingProject.budget_reserved_fen
            + WeddingProject.budget_delivery_reserve_fen
            + amount_fen
            <= WeddingProject.budget_limit_fen,
        )
        .values(budget_reserved_fen=WeddingProject.budget_reserved_fen + amount_fen)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise HTTPException(status_code=409, detail="BUDGET_LIMIT_EXCEEDED")
    entry = BudgetLedger(
        project_id=project.id,
        generation_job_id=job.id,
        reservation_key=reservation_key,
        status=BudgetLedgerStatus.RESERVED,
        reserved_fen=amount_fen,
    )
    job.reserved_amount_fen = amount_fen
    db.add(entry)
    db.flush()
    db.expire(project, ["budget_reserved_fen"])
    return entry


def settle_generation_budget(db: Session, job: GenerationJob, actual_fen: int) -> BudgetLedger:
    """Settle an existing reservation once, releasing unused funds. Worker-facing API."""
    entry = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == job.id)).scalar_one()
    if entry.status == BudgetLedgerStatus.SETTLED:
        if entry.actual_fen != actual_fen:
            raise ValueError("A settled generation reservation cannot be changed")
        return entry
    if entry.status != BudgetLedgerStatus.RESERVED or actual_fen < 0 or actual_fen > entry.reserved_fen:
        raise ValueError("Invalid generation budget settlement")
    result = db.execute(
        update(WeddingProject)
        .where(WeddingProject.id == job.project_id, WeddingProject.budget_reserved_fen >= entry.reserved_fen)
        .values(
            budget_reserved_fen=WeddingProject.budget_reserved_fen - entry.reserved_fen,
            budget_spent_fen=WeddingProject.budget_spent_fen + actual_fen,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ValueError("Wedding budget reservation is inconsistent")
    set_budget_status(entry, BudgetLedgerStatus.SETTLED)
    entry.actual_fen = actual_fen
    entry.released_fen = entry.reserved_fen - actual_fen
    entry.settled_at = utc_now()
    job.actual_cost_fen = actual_fen
    db.flush()
    return entry


def release_generation_budget(db: Session, job: GenerationJob) -> BudgetLedger:
    """Release a reservation when a job is safely cancelled before dispatch."""
    entry = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == job.id)).scalar_one()
    if entry.status == BudgetLedgerStatus.RELEASED:
        return entry
    if entry.status != BudgetLedgerStatus.RESERVED:
        raise ValueError("Only an unsettled reservation can be released")
    result = db.execute(
        update(WeddingProject)
        .where(WeddingProject.id == job.project_id, WeddingProject.budget_reserved_fen >= entry.reserved_fen)
        .values(budget_reserved_fen=WeddingProject.budget_reserved_fen - entry.reserved_fen)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ValueError("Wedding budget reservation is inconsistent")
    set_budget_status(entry, BudgetLedgerStatus.RELEASED)
    entry.released_fen = entry.reserved_fen
    entry.settled_at = utc_now()
    job.reserved_amount_fen = 0
    db.flush()
    return entry


class WeddingPrivateStorage:
    """Private wedding object storage; local files live beside, never under, static uploads."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def put(self, project_id: str, data: bytes, suffix: str, mime: str) -> str:
        key = f"private-wedding/{project_id}/{uuid4().hex}{suffix}"
        if self.settings.storage_driver == "s3":
            self._s3_client().put_object(Bucket=self.settings.wedding_s3_bucket, Key=key, Body=data, ContentType=mime)
            return key
        path = self._local_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def read(self, key: str) -> bytes:
        if self.settings.storage_driver == "s3":
            response = self._s3_client().get_object(Bucket=self.settings.wedding_s3_bucket, Key=key)
            return response["Body"].read()
        return self._local_path(key).read_bytes()

    def delete(self, key: str) -> None:
        if self.settings.storage_driver == "s3":
            self._s3_client().delete_object(Bucket=self.settings.wedding_s3_bucket, Key=key)
            return
        path = self._local_path(key)
        path.unlink(missing_ok=True)

    def _local_path(self, key: str) -> Path:
        pure = PurePosixPath(key)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts or pure.parts[0] != "private-wedding":
            raise ValueError("Invalid private object key")
        uploads = Path(self.settings.local_upload_dir).resolve()
        root = uploads.parent / f"{uploads.name}-private-wedding"
        path = root.joinpath(*pure.parts[1:]).resolve()
        if not path.is_relative_to(root.resolve()):
            raise ValueError("Invalid private object key")
        return path

    def _s3_client(self):
        import boto3

        return boto3.client(
            "s3",
            endpoint_url=self.settings.s3_endpoint_url,
            region_name=self.settings.s3_region,
            aws_access_key_id=self.settings.s3_access_key_id,
            aws_secret_access_key=self.settings.s3_secret_access_key,
        )


def private_storage() -> WeddingPrivateStorage:
    return WeddingPrivateStorage(get_settings())
