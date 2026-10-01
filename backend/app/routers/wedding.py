from __future__ import annotations

import hashlib
import io
import secrets
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Literal

import jwt
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import (
    BudgetLedger,
    ConsentInvite,
    GenerationJob,
    BudgetLedgerStatus,
    ParticipantReviewStatus,
    ParticipantReview,
    RenderVersion,
    RenderVersionStatus,
    ShotCandidate,
    StoryFact,
    StoryVersion,
    StoryVersionStatus,
    StoryboardShot,
    StoryboardShotStatus,
    WeddingAsset,
    WeddingAssetType,
    WeddingConsent,
    WeddingConsentStatus,
    WeddingInviteStatus,
    WeddingJobStatus,
    WeddingParticipant,
    WeddingProject,
    WeddingProjectStatus,
    WeddingQuote,
    utc_now,
)
from app.schemas import (
    CandidateSelectionRequest,
    WeddingCandidateResponse,
    WeddingCapabilitiesResponse,
    ConsentConfirmRequest,
    ConsentInviteCreate,
    ConsentInviteResponse,
    ExportResponse,
    FallbackAcceptRequest,
    FrameApprovalRequest,
    GenerationJobCancelResponse,
    GenerationJobCreate,
    GenerationJobResponse,
    ParticipantReviewRequest,
    ParticipantReviewResponse,
    ParticipantSessionResponse,
    RenderCreateRequest,
    RenderVersionResponse,
    WeddingRenderReviewSummary,
    WeddingShotReviewSummary,
    StoryVersionResponse,
    VersionRequest,
    WeddingAssetResponse,
    WeddingConsentRequest,
    WeddingProjectCreate,
    WeddingProjectDetailResponse,
    WeddingProjectPatch,
    WeddingProjectResponse,
    WeddingParticipantSummary,
    WeddingQuoteResponse,
    StoryboardSubmitRequest,
)
from app.security import hash_code
from app.wedding_service import (
    WeddingActor,
    WeddingPrivateStorage,
    get_wedding_actor,
    private_storage,
    release_generation_budget,
    require_project_owner,
    reserve_generation_budget,
    set_job_status,
    set_budget_status,
    set_consent_status,
    set_invite_status,
    set_project_status,
    set_render_status,
    set_review_status,
    set_shot_status,
    set_story_status,
    validate_active_consents,
    validate_identity_reviews,
)

router = APIRouter(tags=["wedding"])
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_PROJECT_PHOTOS = 30
ALLOWED_IMAGE_FORMATS = {
    "JPEG": ("image/jpeg", ".jpg"),
    "PNG": ("image/png", ".png"),
    "WEBP": ("image/webp", ".webp"),
}
ALLOWED_ASSET_TYPES = {item.value for item in WeddingAssetType}


def _project_response(project: WeddingProject) -> WeddingProjectResponse:
    return WeddingProjectResponse(
        id=project.id,
        title=project.title,
        status=str(project.status),
        version=project.current_version,
        style=project.style,
        target_duration_ms=project.target_duration_ms,
        aspect_ratio=project.aspect_ratio,
        budget_limit_fen=project.budget_limit_fen,
        budget_reserved_fen=project.budget_reserved_fen,
        budget_spent_fen=project.budget_spent_fen,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _asset_response(asset: WeddingAsset) -> WeddingAssetResponse:
    return WeddingAssetResponse(
        id=asset.id,
        asset_type=str(asset.asset_type),
        person_key=asset.person_key,
        mime=asset.mime,
        size_bytes=asset.size_bytes,
        width=asset.width,
        height=asset.height,
        generated=asset.generated,
        created_at=asset.created_at,
    )


def _story_response(story: StoryVersion) -> StoryVersionResponse:
    return StoryVersionResponse(
        id=story.id,
        version=story.version,
        story_text=story.story_text,
        style=story.style,
        status=story.status,
        owner_approved_at=story.owner_approved_at,
        facts=[
            {
                "id": fact.id,
                "source_field": fact.source_field,
                "text": fact.text,
                "confirmed_at": fact.confirmed_at,
            }
            for fact in story.facts
        ],
        shots=[
            {
                "id": shot.id,
                "project_version": shot.project_version,
                "shot_index": shot.shot_index,
                "shot_type": shot.shot_type,
                "duration_ms": shot.duration_ms,
                "person_keys": shot.person_keys,
                "fact_ids": shot.fact_ids,
                "description": shot.description,
                "subtitle": shot.subtitle,
                "creative_scene": shot.creative_scene,
                "frame_asset_id": shot.frame_asset_id,
                "selected_asset_id": shot.selected_asset_id,
                "selected_candidate_id": shot.selected_candidate_id,
                "status": shot.status,
                "identity_reviews": [
                    WeddingShotReviewSummary(
                        person_key=review.person_key,
                        asset_id=review.asset_id,
                        result=review.result,
                        confirmed_at=review.confirmed_at,
                    )
                    for review in sorted(shot.reviews, key=lambda item: (item.person_key, item.confirmed_at))
                    if review.shot_id == shot.id and review.render_version_id is None
                ],
            }
            for shot in sorted(story.shots, key=lambda item: item.shot_index)
        ],
    )


def _render_response(
    render: RenderVersion,
    *,
    participant: bool = False,
    participant_person_key: str | None = None,
) -> RenderVersionResponse:
    return RenderVersionResponse(
        id=render.id,
        project_version=render.project_version,
        version=render.version,
        status=render.status,
        preview_asset_id=render.preview_asset_id,
        export_asset_id=None if participant else render.export_asset_id,
        output_duration_ms=render.output_duration_ms,
        owner_approved_at=render.owner_approved_at,
        created_at=render.created_at,
        identity_reviews=[
            WeddingRenderReviewSummary(
                person_key=review.person_key,
                asset_id=review.asset_id,
                result=review.result,
                confirmed_at=review.confirmed_at,
            )
            for review in sorted(render.participant_reviews, key=lambda item: item.person_key)
            if review.render_version_id == render.id
            and (not participant or review.person_key == participant_person_key)
        ],
    )


def _job_response(job: GenerationJob) -> GenerationJobResponse:
    return GenerationJobResponse(
        id=job.id,
        project_id=job.project_id,
        project_version=job.project_version,
        status=str(job.status),
        progress_stage=job.progress_stage,
        completed_shots=job.completed_shots,
        total_shots=job.total_shots,
        reserved_amount_fen=job.reserved_amount_fen,
        actual_cost_fen=job.actual_cost_fen,
        error_code=job.error_code,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _record_audit(
    db: Session,
    project: WeddingProject,
    actor: WeddingActor,
    action: str,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    from app.models import WeddingAuditEvent

    db.add(
        WeddingAuditEvent(
            project_id=project.id,
            actor_kind=actor.kind,
            actor_id=actor.actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            project_version=project.current_version,
            metadata_json=metadata or {},
        )
    )


def _current_story(db: Session, project: WeddingProject) -> StoryVersion:
    story = db.execute(
        select(StoryVersion).where(
            StoryVersion.project_id == project.id,
            StoryVersion.version == project.current_version,
        )
    ).scalar_one_or_none()
    if story is None:
        raise HTTPException(status_code=409, detail="STORYBOARD_REQUIRED")
    return story


def _check_version(project: WeddingProject, version: int) -> None:
    if version != project.current_version:
        raise HTTPException(status_code=409, detail="VERSION_CONFLICT")


def _set_project_status(project: WeddingProject, status_value: WeddingProjectStatus) -> None:
    try:
        set_project_status(project, status_value)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="PROJECT_STATE_TRANSITION_INVALID") from exc


def _participant_for_actor(db: Session, project: WeddingProject, actor: WeddingActor) -> WeddingParticipant:
    if actor.kind == "participant" and actor.project_id == project.id and actor.participant_id:
        participant = db.get(WeddingParticipant, actor.participant_id)
        if participant is None or participant.project_id != project.id or participant.consent_status != "active":
            raise HTTPException(status_code=404, detail="Wedding resource not found")
        return participant
    if actor.kind == "owner" and actor.owner_id == project.owner_id:
        participant = db.execute(
            select(WeddingParticipant).where(
                WeddingParticipant.project_id == project.id,
                WeddingParticipant.person_key == "A",
                WeddingParticipant.is_owner.is_(True),
            )
        ).scalar_one_or_none()
        if participant is None:
            raise HTTPException(status_code=409, detail="OWNER_PARTICIPANT_REQUIRED")
        return participant
    raise HTTPException(status_code=404, detail="Wedding resource not found")


def _validate_image(data: bytes, declared_mime: str | None, asset_type: str) -> tuple[str, str, int, int]:
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="IMAGE_SIZE_INVALID")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image_format = image.format
            width, height = image.size
            image.verify()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(status_code=415, detail="INVALID_IMAGE") from exc
    detected = ALLOWED_IMAGE_FORMATS.get(image_format or "")
    if detected is None:
        raise HTTPException(status_code=415, detail="IMAGE_FORMAT_UNSUPPORTED")
    mime, suffix = detected
    if declared_mime and declared_mime not in {mime, "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="IMAGE_MIME_MISMATCH")
    if asset_type == WeddingAssetType.PHOTO.value and max(width, height) < 1024:
        raise HTTPException(status_code=422, detail="IMAGE_RESOLUTION_TOO_LOW")
    return mime, suffix, width, height


def _can_read_asset(db: Session, asset: WeddingAsset, actor: WeddingActor) -> bool:
    project = db.get(WeddingProject, asset.project_id)
    if project is None:
        return False
    try:
        validate_active_consents(db, project)
    except HTTPException:
        return False
    if asset.asset_type in {WeddingAssetType.PREVIEW_VIDEO, WeddingAssetType.FINAL_VIDEO}:
        if asset.asset_type == WeddingAssetType.FINAL_VIDEO and actor.kind != "owner":
            return False
        expected_status = (
            RenderVersionStatus.APPROVED
            if asset.asset_type == WeddingAssetType.FINAL_VIDEO
            else None
        )
        render_query = select(RenderVersion).where(
            RenderVersion.project_id == project.id,
            RenderVersion.project_version == project.current_version,
        )
        if asset.asset_type == WeddingAssetType.FINAL_VIDEO:
            render_query = render_query.where(
                RenderVersion.status == expected_status,
                RenderVersion.owner_approved_at.is_not(None),
                RenderVersion.export_asset_id == asset.id,
            )
        else:
            render_query = render_query.where(
                RenderVersion.status.in_([RenderVersionStatus.READY, RenderVersionStatus.APPROVED]),
                RenderVersion.preview_asset_id == asset.id,
            )
        return db.execute(render_query).scalars().first() is not None
    if actor.kind == "owner":
        return actor.owner_id == project.owner_id
    if actor.project_id != project.id or not actor.person_key:
        return False
    if asset.asset_type == WeddingAssetType.FINAL_VIDEO:
        return False
    if asset.person_key == actor.person_key:
        return True
    if asset.asset_type == WeddingAssetType.PREVIEW_VIDEO:
        return True
    if asset.asset_type not in {
        WeddingAssetType.PHOTO,
        WeddingAssetType.STORYBOARD_FRAME,
        WeddingAssetType.GENERATED_FRAME,
        WeddingAssetType.VIDEO_CANDIDATE,
    }:
        return False
    linked_shot = db.execute(
        select(StoryboardShot).where(
            StoryboardShot.project_id == project.id,
            (StoryboardShot.frame_asset_id == asset.id) | (StoryboardShot.selected_asset_id == asset.id),
        )
    ).scalar_one_or_none()
    if linked_shot is not None and actor.person_key in linked_shot.person_keys:
        return True
    candidate_shot_ids = db.execute(
        select(ShotCandidate.shot_id).where(
            ShotCandidate.project_id == project.id,
            ShotCandidate.asset_id == asset.id,
        )
    ).scalars()
    return any(
        (shot := db.get(StoryboardShot, shot_id)) is not None and actor.person_key in shot.person_keys
        for shot_id in candidate_shot_ids
    )


def _readable_project(db: Session, actor: WeddingActor, project_id: str) -> WeddingProject:
    project = db.get(WeddingProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Wedding project not found")
    if actor.kind == "owner" and actor.owner_id == project.owner_id:
        return project
    if actor.kind == "participant" and actor.project_id == project.id and actor.person_key in {"A", "B"}:
        return project
    raise HTTPException(status_code=404, detail="Wedding project not found")


def _capability(
    provider: str,
    *,
    demo: bool,
) -> dict[str, object]:
    if demo:
        return {
            "provider": "local_demo",
            "configured": True,
            "available": True,
            "reason": "DEMO_ONLY_NO_AI_MOTION",
        }
    configured = provider.strip().lower() not in {"", "disabled"}
    return {
        "provider": provider,
        "configured": configured,
        "available": False,
        "reason": "LIVE_ADAPTER_UNAVAILABLE" if configured else "DISABLED",
    }


def _check_job_prerequisites(db: Session, project: WeddingProject, version: int) -> StoryVersion:
    _check_version(project, version)
    validate_active_consents(db, project)
    story = _current_story(db, project)
    if story.status != "approved" or story.owner_approved_at is None:
        raise HTTPException(status_code=409, detail="STORYBOARD_APPROVAL_REQUIRED")
    if project.frame_approved_version != project.current_version:
        raise HTTPException(status_code=409, detail="STATIC_FRAME_APPROVAL_REQUIRED")
    validate_identity_reviews(db, project, story)
    return story


@router.post("/wedding-projects", response_model=WeddingProjectResponse, status_code=201)
def create_wedding_project(
    body: WeddingProjectCreate,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> WeddingProjectResponse:
    if actor.kind != "owner" or actor.owner_id is None:
        raise HTTPException(status_code=403, detail="OWNER_SCOPE_REQUIRED")
    project = WeddingProject(owner_id=actor.owner_id, title=body.title, style=body.style)
    db.add(project)
    db.flush()
    db.add(
        WeddingParticipant(
            project_id=project.id,
            person_key="A",
            display_name=body.owner_name,
            is_owner=True,
            user_id=actor.owner_id,
            consent_status="pending",
        )
    )
    _record_audit(db, project, actor, "project.created", "project", project.id)
    db.commit()
    db.refresh(project)
    return _project_response(project)


@router.get("/wedding-projects", response_model=list[WeddingProjectResponse])
def list_wedding_projects(
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> list[WeddingProjectResponse]:
    if actor.kind != "owner" or actor.owner_id is None:
        raise HTTPException(status_code=403, detail="OWNER_SCOPE_REQUIRED")
    projects = db.execute(
        select(WeddingProject).where(WeddingProject.owner_id == actor.owner_id).order_by(WeddingProject.updated_at.desc())
    ).scalars()
    return [_project_response(project) for project in projects]


@router.get("/wedding-capabilities", response_model=WeddingCapabilitiesResponse)
def get_wedding_capabilities(
    actor: WeddingActor = Depends(get_wedding_actor),
) -> WeddingCapabilitiesResponse:
    del actor
    settings = get_settings()
    demo = settings.wedding_media_mode.strip().lower() == "demo"
    configured_ffmpeg = settings.wedding_ffmpeg_path
    ffmpeg_available = bool(
        (Path(configured_ffmpeg).is_file() if configured_ffmpeg else shutil.which("ffmpeg"))
    )
    licensed_music_available = bool(
        settings.wedding_music_file
        and settings.wedding_music_license_id
        and Path(settings.wedding_music_file).is_file()
    )
    providers = {
        "story": _capability(settings.wedding_story_provider, demo=demo),
        "frame": _capability(settings.wedding_frame_provider, demo=demo),
        "character_video": _capability(
            settings.wedding_character_video_provider,
            demo=demo,
        ),
        "scene_video": _capability(settings.wedding_scene_video_provider, demo=demo),
    }
    return WeddingCapabilitiesResponse(
        mode="demo" if demo else "disabled",
        demo_only=demo,
        providers=providers,
        ffmpeg_available=ffmpeg_available,
        licensed_music_available=licensed_music_available,
        real_export_available=ffmpeg_available and licensed_music_available and not demo,
    )


@router.get("/wedding-projects/{project_id}", response_model=WeddingProjectDetailResponse)
def get_wedding_project(
    project_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> WeddingProjectDetailResponse:
    project = _readable_project(db, actor, project_id)
    story = db.execute(
        select(StoryVersion)
        .where(StoryVersion.project_id == project.id, StoryVersion.version == project.current_version)
    ).scalar_one_or_none()
    participants = db.execute(
        select(WeddingParticipant).where(WeddingParticipant.project_id == project.id).order_by(WeddingParticipant.person_key)
    ).scalars()
    response = WeddingProjectDetailResponse(
        **_project_response(project).model_dump(),
        participants=[
            {
                "person_key": participant.person_key,
                "display_name": participant.display_name,
                "consent_status": participant.consent_status,
                "confirmed_at": participant.confirmed_at,
            }
            for participant in participants
        ],
        story_text=story.story_text if story and actor.kind == "owner" else None,
    )
    return response


@router.get("/wedding-projects/{project_id}/storyboard", response_model=StoryVersionResponse)
def get_current_wedding_storyboard(
    project_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> StoryVersionResponse:
    project = _readable_project(db, actor, project_id)
    story = _current_story(db, project)
    response = _story_response(story)
    if actor.kind == "owner":
        return response
    if story.status != StoryVersionStatus.APPROVED:
        raise HTTPException(status_code=409, detail="STORYBOARD_NOT_APPROVED")
    visible_shots = [
        shot.model_copy(
            update={
                "identity_reviews": [
                    review for review in shot.identity_reviews if review.person_key == actor.person_key
                ]
            }
        )
        for shot in response.shots
        if actor.person_key in shot.person_keys
    ]
    visible_fact_ids = {fact_id for shot in visible_shots for fact_id in shot.fact_ids}
    return response.model_copy(
        update={
            "story_text": "",
            "shots": visible_shots,
            "facts": [fact for fact in response.facts if fact.id in visible_fact_ids],
        }
    )


@router.get("/wedding-projects/{project_id}/assets", response_model=list[WeddingAssetResponse])
def list_wedding_assets(
    project_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> list[WeddingAssetResponse]:
    project = _readable_project(db, actor, project_id)
    assets = db.execute(
        select(WeddingAsset).where(WeddingAsset.project_id == project.id).order_by(WeddingAsset.created_at)
    ).scalars()
    return [_asset_response(asset) for asset in assets if _can_read_asset(db, asset, actor)]


@router.get("/wedding-projects/{project_id}/generation-jobs", response_model=list[GenerationJobResponse])
def list_wedding_generation_jobs(
    project_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> list[GenerationJobResponse]:
    project = _readable_project(db, actor, project_id)
    query = select(GenerationJob).where(GenerationJob.project_id == project.id).order_by(GenerationJob.created_at)
    if actor.kind == "participant":
        visible_shot_ids = {
            shot.id
            for shot in db.execute(
                select(StoryboardShot).where(
                    StoryboardShot.project_id == project.id,
                    StoryboardShot.project_version == project.current_version,
                )
            ).scalars()
            if actor.person_key in shot.person_keys
        }
        jobs = db.execute(query).scalars()
        return [
            _job_response(job)
            for job in jobs
            if (
                job.target_shot_id is None and job.project_version == project.current_version
            )
            or job.target_shot_id in visible_shot_ids
        ]
    return [_job_response(job) for job in db.execute(query).scalars()]


@router.get("/wedding-projects/{project_id}/candidates", response_model=list[WeddingCandidateResponse])
def list_wedding_candidates(
    project_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> list[WeddingCandidateResponse]:
    project = _readable_project(db, actor, project_id)
    candidates = db.execute(
        select(ShotCandidate)
        .join(StoryboardShot, StoryboardShot.id == ShotCandidate.shot_id)
        .where(ShotCandidate.project_id == project.id)
        .order_by(ShotCandidate.created_at)
    ).scalars()
    visible: list[WeddingCandidateResponse] = []
    for candidate in candidates:
        shot = db.get(StoryboardShot, candidate.shot_id)
        asset = db.get(WeddingAsset, candidate.asset_id)
        if shot is None or asset is None:
            continue
        if actor.kind == "participant" and actor.person_key not in shot.person_keys:
            continue
        if not _can_read_asset(db, asset, actor):
            continue
        visible.append(
            WeddingCandidateResponse(
                id=candidate.id,
                shot_id=candidate.shot_id,
                generation_job_id=candidate.generation_job_id,
                asset_id=candidate.asset_id,
                asset=_asset_response(asset),
                provider=candidate.provider,
                model_version=candidate.model_version,
                quality_flags=candidate.quality_flags,
                review_result=candidate.review_result,
                actual_cost_fen=candidate.actual_cost_fen,
                created_at=candidate.created_at,
            )
        )
    return visible


@router.patch("/wedding-projects/{project_id}", response_model=WeddingProjectResponse)
def update_wedding_project(
    project_id: str,
    body: WeddingProjectPatch,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> WeddingProjectResponse:
    project = require_project_owner(db, actor, project_id)
    _check_version(project, body.version)
    changed = False
    if body.title is not None and body.title != project.title:
        project.title = body.title
        changed = True
    if body.style is not None and body.style != project.style:
        project.style = body.style
        changed = True
    if changed:
        project.current_version += 1
        project.frame_approved_version = None
        _set_project_status(project, WeddingProjectStatus.STORY_REVIEW)
        current_story = db.execute(
            select(StoryVersion).where(
                StoryVersion.project_id == project.id,
                StoryVersion.version == body.version,
            )
        ).scalar_one_or_none()
        if current_story is not None:
            set_story_status(current_story, StoryVersionStatus.SUPERSEDED)
        for job in db.execute(
            select(GenerationJob).where(
                GenerationJob.project_id == project.id,
                GenerationJob.status.in_([WeddingJobStatus.QUEUED, WeddingJobStatus.PROCESSING]),
            )
        ).scalars():
            if job.status == WeddingJobStatus.QUEUED:
                set_job_status(job, WeddingJobStatus.CANCELLED)
                release_generation_budget(db, job)
                job.finished_at = utc_now()
            elif job.status == WeddingJobStatus.PROCESSING:
                set_job_status(job, WeddingJobStatus.CANCEL_REQUESTED)
                job.cancel_requested_at = utc_now()
        _record_audit(db, project, actor, "project.updated", "project", project.id, {"version": project.current_version})
    db.commit()
    db.refresh(project)
    return _project_response(project)


@router.delete("/wedding-projects/{project_id}", status_code=204)
def delete_wedding_project(
    project_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
    storage: WeddingPrivateStorage = Depends(private_storage),
) -> Response:
    project = require_project_owner(db, actor, project_id)
    # Remove private bytes before deleting metadata. No public URL exists to invalidate.
    for asset in list(project.assets):
        storage.delete(asset.object_key)
    db.delete(project)
    db.commit()
    return Response(status_code=204)


@router.post("/wedding-projects/{project_id}/self-consent", response_model=WeddingParticipantSummary)
def confirm_owner_consent(
    project_id: str,
    body: WeddingConsentRequest,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    project = require_project_owner(db, actor, project_id)
    participant = db.execute(
        select(WeddingParticipant).where(
            WeddingParticipant.project_id == project.id,
            WeddingParticipant.person_key == "A",
            WeddingParticipant.is_owner.is_(True),
        )
    ).scalar_one()
    now = utc_now()
    set_consent_status(participant, WeddingConsentStatus.ACTIVE)
    participant.consent_scope = sorted(set(body.scope))
    participant.provider_list = sorted(set(body.provider_list))
    participant.terms_version = body.terms_version
    participant.confirmed_at = now
    participant.revoked_at = None
    for previous in db.execute(
        select(WeddingConsent).where(
            WeddingConsent.participant_id == participant.id,
            WeddingConsent.revoked_at.is_(None),
        )
    ).scalars():
        previous.revoked_at = now
    db.add(
        WeddingConsent(
            project_id=project.id,
            participant_id=participant.id,
            scope=participant.consent_scope,
            provider_list=participant.provider_list,
            terms_version=body.terms_version,
            confirmed_at=now,
        )
    )
    _record_audit(db, project, actor, "consent.owner_confirmed", "participant", participant.id)
    partner = db.execute(
        select(WeddingParticipant).where(
            WeddingParticipant.project_id == project.id,
            WeddingParticipant.person_key == "B",
            WeddingParticipant.consent_status == "active",
        )
    ).scalar_one_or_none()
    if partner is not None and project.status == WeddingProjectStatus.AWAITING_CONSENT:
        set_project_status(project, WeddingProjectStatus.STORY_REVIEW)
    db.commit()
    return {
        "person_key": participant.person_key,
        "display_name": participant.display_name,
        "consent_status": participant.consent_status,
        "confirmed_at": participant.confirmed_at,
    }


@router.post("/wedding-projects/{project_id}/consent-invites", response_model=ConsentInviteResponse, status_code=201)
def create_consent_invite(
    project_id: str,
    body: ConsentInviteCreate,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> ConsentInviteResponse:
    project = require_project_owner(db, actor, project_id)
    participant = db.execute(
        select(WeddingParticipant).where(
            WeddingParticipant.project_id == project.id,
            WeddingParticipant.person_key == "B",
        )
    ).scalar_one_or_none()
    if participant is None:
        participant = WeddingParticipant(project_id=project.id, person_key="B", display_name=body.display_name)
        db.add(participant)
        db.flush()
    else:
        if participant.consent_status == "active":
            raise HTTPException(status_code=409, detail="PARTICIPANT_ALREADY_CONSENTED")
        participant.display_name = body.display_name
        set_consent_status(participant, WeddingConsentStatus.PENDING)
        participant.session_token_hash = None
        participant.session_expires_at = None
    for previous in db.execute(
        select(ConsentInvite).where(
            ConsentInvite.project_id == project.id,
            ConsentInvite.participant_id == participant.id,
            ConsentInvite.status == WeddingInviteStatus.ACTIVE,
            ConsentInvite.consumed_at.is_(None),
            ConsentInvite.revoked_at.is_(None),
        )
    ).scalars():
        previous.revoked_at = utc_now()
        set_invite_status(previous, WeddingInviteStatus.REVOKED)
    raw_token = secrets.token_urlsafe(32)
    expires_at = utc_now() + timedelta(days=7)
    invite = ConsentInvite(
        project_id=project.id,
        participant_id=participant.id,
        token_hash=hash_code(raw_token),
        scope=["portrait_processing", "preview_review", "storage"],
        provider_list=sorted(set(body.provider_list)),
        terms_version=body.terms_version,
        status=WeddingInviteStatus.ACTIVE,
        expires_at=expires_at,
    )
    db.add(invite)
    _set_project_status(project, WeddingProjectStatus.AWAITING_CONSENT)
    _record_audit(db, project, actor, "consent.invite_created", "participant", participant.id)
    db.commit()
    db.refresh(invite)
    return ConsentInviteResponse(
        id=invite.id,
        invite_token=raw_token,
        person_key="B",
        expires_at=invite.expires_at,
    )


@router.post("/consent-invites/{token}/confirm", response_model=ParticipantSessionResponse)
def confirm_consent_invite(
    token: str,
    body: ConsentConfirmRequest,
    db: Session = Depends(get_db),
) -> ParticipantSessionResponse:
    invite = db.execute(select(ConsentInvite).where(ConsentInvite.token_hash == hash_code(token))).scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="Consent invite not found")
    now = utc_now()
    expires_at = invite.expires_at if invite.expires_at.tzinfo else invite.expires_at.replace(tzinfo=now.tzinfo)
    if (
        invite.status != WeddingInviteStatus.ACTIVE
        or invite.revoked_at is not None
        or invite.consumed_at is not None
    ):
        raise HTTPException(status_code=409, detail="CONSENT_INVITE_ALREADY_USED")
    if expires_at <= now:
        expired = db.execute(
            update(ConsentInvite)
            .where(
                ConsentInvite.token_hash == hash_code(token),
                ConsentInvite.status == WeddingInviteStatus.ACTIVE,
                ConsentInvite.consumed_at.is_(None),
                ConsentInvite.revoked_at.is_(None),
                ConsentInvite.expires_at <= now,
            )
            .values(status=WeddingInviteStatus.EXPIRED)
            .execution_options(synchronize_session=False)
        )
        if expired.rowcount == 1:
            db.commit()
            raise HTTPException(status_code=410, detail="CONSENT_INVITE_EXPIRED")
        db.rollback()
        raise HTTPException(status_code=409, detail="CONSENT_INVITE_ALREADY_USED")
    if body.terms_version != invite.terms_version:
        raise HTTPException(status_code=409, detail="CONSENT_TERMS_VERSION_MISMATCH")
    participant = db.get(WeddingParticipant, invite.participant_id)
    project = db.get(WeddingProject, invite.project_id)
    if participant is None or project is None:
        raise HTTPException(status_code=404, detail="Consent invite not found")
    if body.accepted and not body.display_name:
        raise HTTPException(status_code=422, detail="DISPLAY_NAME_REQUIRED")
    claimed = db.execute(
        update(ConsentInvite)
        .where(
            ConsentInvite.token_hash == hash_code(token),
            ConsentInvite.status == WeddingInviteStatus.ACTIVE,
            ConsentInvite.consumed_at.is_(None),
            ConsentInvite.revoked_at.is_(None),
            ConsentInvite.expires_at > now,
        )
        .values(status=WeddingInviteStatus.CONSUMED, consumed_at=now)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail="CONSENT_INVITE_ALREADY_USED")
    db.refresh(invite, attribute_names=["status", "consumed_at"])
    if not body.accepted:
        set_consent_status(participant, WeddingConsentStatus.DECLINED)
        participant.session_token_hash = None
        _record_audit(
            db,
            project,
            WeddingActor(kind="participant", actor_id=participant.id, participant_id=participant.id, project_id=project.id, person_key=participant.person_key),
            "consent.declined",
            "participant",
            participant.id,
        )
        db.commit()
        return ParticipantSessionResponse(
            accepted=False,
            project_id=project.id,
            participant={
                "person_key": participant.person_key,
                "display_name": participant.display_name,
                "consent_status": participant.consent_status,
                "confirmed_at": participant.confirmed_at,
            },
        )
    participant.display_name = body.display_name
    set_consent_status(participant, WeddingConsentStatus.ACTIVE)
    participant.consent_scope = invite.scope
    participant.provider_list = invite.provider_list
    participant.terms_version = invite.terms_version
    participant.confirmed_at = now
    participant.revoked_at = None
    settings = get_settings()
    session_expires = now + timedelta(hours=24)
    participant_token = jwt.encode(
        {
            "sub": participant.id,
            "participant_id": participant.id,
            "project_id": project.id,
            "token_type": "wedding_participant",
            "iat": int(now.timestamp()),
            "exp": int(session_expires.timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    participant.session_token_hash = hashlib.sha256(participant_token.encode("utf-8")).hexdigest()
    participant.session_expires_at = session_expires
    for previous in db.execute(
        select(WeddingConsent).where(
            WeddingConsent.participant_id == participant.id,
            WeddingConsent.revoked_at.is_(None),
        )
    ).scalars():
        previous.revoked_at = now
    db.add(
        WeddingConsent(
            project_id=project.id,
            participant_id=participant.id,
            scope=invite.scope,
            provider_list=invite.provider_list,
            terms_version=invite.terms_version,
            confirmed_at=now,
        )
    )
    owner = db.execute(
        select(WeddingParticipant).where(
            WeddingParticipant.project_id == project.id,
            WeddingParticipant.person_key == "A",
            WeddingParticipant.consent_status == "active",
        )
    ).scalar_one_or_none()
    if owner is not None:
        _set_project_status(project, WeddingProjectStatus.STORY_REVIEW)
    actor = WeddingActor(
        kind="participant",
        actor_id=participant.id,
        participant_id=participant.id,
        project_id=project.id,
        person_key=participant.person_key,
    )
    _record_audit(db, project, actor, "consent.participant_confirmed", "participant", participant.id)
    db.commit()
    return ParticipantSessionResponse(
        access_token=participant_token,
        accepted=True,
        project_id=project.id,
        participant={
            "person_key": participant.person_key,
            "display_name": participant.display_name,
            "consent_status": participant.consent_status,
            "confirmed_at": participant.confirmed_at,
        },
    )


@router.post("/wedding-projects/{project_id}/consent/revoke")
def revoke_consent(
    project_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    project = db.execute(
        select(WeddingProject).where(WeddingProject.id == project_id).with_for_update()
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Wedding project not found")
    participant = _participant_for_actor(db, project, actor)
    now = utc_now()
    set_consent_status(participant, WeddingConsentStatus.REVOKED)
    participant.revoked_at = now
    participant.session_token_hash = None
    participant.session_expires_at = None
    for consent in db.execute(
        select(WeddingConsent).where(
            WeddingConsent.participant_id == participant.id,
            WeddingConsent.revoked_at.is_(None),
        )
    ).scalars():
        consent.revoked_at = now
    _set_project_status(project, WeddingProjectStatus.AWAITING_CONSENT)
    for job in db.execute(
        select(GenerationJob).where(
            GenerationJob.project_id == project.id,
            GenerationJob.status.in_([WeddingJobStatus.QUEUED, WeddingJobStatus.PROCESSING]),
        )
    ).scalars():
        if job.status == WeddingJobStatus.QUEUED:
            set_job_status(job, WeddingJobStatus.CANCELLED)
            release_generation_budget(db, job)
            job.finished_at = now
        else:
            set_job_status(job, WeddingJobStatus.CANCEL_REQUESTED)
            job.cancel_requested_at = now
    for render in db.execute(
        select(RenderVersion).where(
            RenderVersion.project_id == project.id,
            RenderVersion.status.in_(
                [
                    RenderVersionStatus.QUEUED,
                    RenderVersionStatus.PROCESSING,
                    RenderVersionStatus.READY,
                    RenderVersionStatus.APPROVED,
                ]
            ),
        )
    ).scalars():
        set_render_status(render, RenderVersionStatus.CANCELLED)
        render.owner_approved_at = None
        render.preview_asset_id = None
        render.export_asset_id = None
        _record_audit(db, project, actor, "render.cancelled_consent", "render_version", render.id)
    _record_audit(db, project, actor, "consent.revoked", "participant", participant.id)
    db.commit()
    return {"revoked": True, "person_key": participant.person_key, "revoked_at": now}


@router.post("/wedding-projects/{project_id}/assets", response_model=WeddingAssetResponse, status_code=201)
async def upload_wedding_asset(
    project_id: str,
    file: UploadFile = File(...),
    person_key: Literal["A", "B"] | None = Form(None),
    asset_type: str = Form("photo"),
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
    storage: WeddingPrivateStorage = Depends(private_storage),
) -> WeddingAssetResponse:
    project = db.get(WeddingProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Wedding project not found")
    if asset_type not in ALLOWED_ASSET_TYPES:
        raise HTTPException(status_code=422, detail="ASSET_TYPE_UNSUPPORTED")
    if asset_type not in {WeddingAssetType.PHOTO.value, WeddingAssetType.STORYBOARD_FRAME.value}:
        raise HTTPException(status_code=422, detail="ASSET_TYPE_UPLOAD_UNSUPPORTED")
    if asset_type in {WeddingAssetType.PHOTO.value, WeddingAssetType.STORYBOARD_FRAME.value} and person_key is None:
        raise HTTPException(status_code=422, detail="PERSON_KEY_REQUIRED")
    if actor.kind == "owner":
        if actor.owner_id != project.owner_id:
            raise HTTPException(status_code=404, detail="Wedding project not found")
        if person_key != "A":
            raise HTTPException(status_code=403, detail="SUBJECT_UPLOAD_SCOPE_REQUIRED")
    else:
        if actor.project_id != project.id or actor.participant_id is None:
            raise HTTPException(status_code=404, detail="Wedding project not found")
        participant = db.get(WeddingParticipant, actor.participant_id)
        if (
            participant is None
            or participant.project_id != project.id
            or participant.person_key != "B"
            or actor.person_key != "B"
        ):
            raise HTTPException(status_code=404, detail="Wedding project not found")
        if person_key != participant.person_key or asset_type != WeddingAssetType.PHOTO.value:
            raise HTTPException(status_code=403, detail="SUBJECT_UPLOAD_SCOPE_REQUIRED")
    subject = db.execute(
        select(WeddingParticipant).where(
            WeddingParticipant.project_id == project.id,
            WeddingParticipant.person_key == person_key,
        )
    ).scalar_one_or_none()
    if (
        subject is None
        or subject.consent_status != WeddingConsentStatus.ACTIVE
        or subject.confirmed_at is None
        or subject.revoked_at is not None
        or "portrait_processing" not in (subject.consent_scope or [])
    ):
        raise HTTPException(status_code=409, detail="SUBJECT_CONSENT_REQUIRED")
    validate_active_consents(db, project)
    if asset_type == WeddingAssetType.PHOTO.value:
        photo_count = db.execute(
            select(WeddingAsset).where(
                WeddingAsset.project_id == project.id,
                WeddingAsset.asset_type == WeddingAssetType.PHOTO,
            )
        ).scalars().all()
        if len(photo_count) >= MAX_PROJECT_PHOTOS:
            raise HTTPException(status_code=413, detail="PROJECT_PHOTO_LIMIT_EXCEEDED")
    data = await file.read(MAX_IMAGE_BYTES + 1)
    mime, suffix, width, height = _validate_image(data, file.content_type, asset_type)
    object_key = storage.put(project.id, data, suffix, mime)
    asset = WeddingAsset(
        project_id=project.id,
        uploaded_by_user_id=actor.owner_id,
        person_key=person_key,
        asset_type=WeddingAssetType(asset_type),
        object_key=object_key,
        sha256=hashlib.sha256(data).hexdigest(),
        mime=mime,
        size_bytes=len(data),
        width=width,
        height=height,
    )
    db.add(asset)
    _record_audit(db, project, actor, "asset.uploaded", "asset", asset.id, {"asset_type": asset_type})
    try:
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(object_key)
        raise
    db.refresh(asset)
    return _asset_response(asset)


@router.get("/wedding-assets/{asset_id}")
def read_wedding_asset(
    asset_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
    storage: WeddingPrivateStorage = Depends(private_storage),
) -> Response:
    asset = db.get(WeddingAsset, asset_id)
    if asset is None or not _can_read_asset(db, asset, actor):
        raise HTTPException(status_code=404, detail="Wedding asset not found")
    data = storage.read(asset.object_key)
    return Response(
        content=data,
        media_type=asset.mime,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete("/wedding-projects/{project_id}/assets/{asset_id}", status_code=204)
def delete_wedding_asset(
    project_id: str,
    asset_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
    storage: WeddingPrivateStorage = Depends(private_storage),
) -> Response:
    project = db.get(WeddingProject, project_id)
    asset = db.get(WeddingAsset, asset_id)
    if project is None or asset is None or asset.project_id != project.id:
        raise HTTPException(status_code=404, detail="Wedding asset not found")
    if actor.kind == "owner":
        if actor.owner_id != project.owner_id:
            raise HTTPException(status_code=404, detail="Wedding asset not found")
    elif actor.project_id != project.id or actor.person_key != asset.person_key:
        raise HTTPException(status_code=404, detail="Wedding asset not found")
    storage.delete(asset.object_key)
    for shot in db.execute(
        select(StoryboardShot).where(
            StoryboardShot.project_id == project.id,
            (StoryboardShot.frame_asset_id == asset.id)
            | (StoryboardShot.selected_asset_id == asset.id),
        )
    ).scalars():
        shot.frame_asset_id = None
        shot.selected_asset_id = None
        set_shot_status(shot, StoryboardShotStatus.FRAME_PENDING)
        project.frame_approved_version = None
        _set_project_status(project, WeddingProjectStatus.FRAME_REVIEW)
    db.delete(asset)
    _record_audit(db, project, actor, "asset.deleted", "asset", asset_id)
    db.commit()
    return Response(status_code=204)


@router.post("/wedding-projects/{project_id}/storyboard", response_model=StoryVersionResponse, status_code=201)
def submit_storyboard(
    project_id: str,
    body: StoryboardSubmitRequest,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> StoryVersionResponse:
    project = require_project_owner(db, actor, project_id)
    _check_version(project, body.version)
    existing = db.execute(
        select(StoryVersion).where(StoryVersion.project_id == project.id, StoryVersion.version == body.version)
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="STORYBOARD_VERSION_ALREADY_EXISTS")
    if len({shot.shot_index for shot in body.shots}) != len(body.shots):
        raise HTTPException(status_code=422, detail="SHOT_INDEX_DUPLICATE")
    story = StoryVersion(
        project_id=project.id,
        version=body.version,
        story_text=body.story_text,
        style=project.style,
        status="draft",
    )
    db.add(story)
    db.flush()
    facts = [
        StoryFact(story_version_id=story.id, source_field=fact.source_field, text=fact.text)
        for fact in body.facts
    ]
    db.add_all(facts)
    db.flush()
    shots = []
    for shot in body.shots:
        if any(index < 0 or index >= len(facts) for index in shot.fact_indexes):
            raise HTTPException(status_code=422, detail="FACT_REFERENCE_INVALID")
        if shot.shot_type == "person_ai" and not shot.person_keys:
            raise HTTPException(status_code=422, detail="PERSON_KEYS_REQUIRED")
        shots.append(
            StoryboardShot(
                project_id=project.id,
                story_version_id=story.id,
                project_version=project.current_version,
                shot_index=shot.shot_index,
                shot_type=shot.shot_type,
                duration_ms=shot.duration_ms,
                person_keys=shot.person_keys,
                fact_ids=[facts[index].id for index in shot.fact_indexes],
                description=shot.description,
                subtitle=shot.subtitle,
                creative_scene=shot.creative_scene,
                status="draft",
            )
        )
    db.add_all(shots)
    _set_project_status(project, WeddingProjectStatus.STORY_REVIEW)
    _record_audit(db, project, actor, "storyboard.submitted", "story_version", story.id)
    db.commit()
    db.refresh(story)
    return _story_response(story)


@router.put("/wedding-projects/{project_id}/storyboard", response_model=StoryVersionResponse)
def update_storyboard_draft(
    project_id: str,
    body: StoryboardSubmitRequest,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> StoryVersionResponse:
    project = require_project_owner(db, actor, project_id)
    _check_version(project, body.version)
    story = _current_story(db, project)
    if story.status != StoryVersionStatus.DRAFT or story.owner_approved_at is not None:
        raise HTTPException(status_code=409, detail="APPROVED_STORYBOARD_IMMUTABLE")
    if len({shot.shot_index for shot in body.shots}) != len(body.shots):
        raise HTTPException(status_code=422, detail="SHOT_INDEX_DUPLICATE")
    if any(index < 0 or index >= len(body.facts) for shot in body.shots for index in shot.fact_indexes):
        raise HTTPException(status_code=422, detail="FACT_REFERENCE_INVALID")
    if any(shot.shot_type == "person_ai" and not shot.person_keys for shot in body.shots):
        raise HTTPException(status_code=422, detail="PERSON_KEYS_REQUIRED")

    # Only the unapproved current draft is replaceable. Approved facts, shots, renders,
    # and exports keep their original version and IDs.
    story.shots.clear()
    story.facts.clear()
    db.flush()
    story.story_text = body.story_text
    story.style = project.style
    for fact in body.facts:
        story.facts.append(StoryFact(source_field=fact.source_field, text=fact.text))
    db.flush()
    facts = list(story.facts)
    for shot in body.shots:
        story.shots.append(
            StoryboardShot(
                project_id=project.id,
                story_version_id=story.id,
                project_version=project.current_version,
                shot_index=shot.shot_index,
                shot_type=shot.shot_type,
                duration_ms=shot.duration_ms,
                person_keys=shot.person_keys,
                fact_ids=[facts[index].id for index in shot.fact_indexes],
                description=shot.description,
                subtitle=shot.subtitle,
                creative_scene=shot.creative_scene,
                status=StoryboardShotStatus.DRAFT,
            )
        )
    project.frame_approved_version = None
    _set_project_status(project, WeddingProjectStatus.STORY_REVIEW)
    _record_audit(db, project, actor, "storyboard.draft_updated", "story_version", story.id)
    db.commit()
    db.refresh(story)
    return _story_response(story)


@router.post("/wedding-projects/{project_id}/storyboard/approve", response_model=StoryVersionResponse)
def approve_storyboard(
    project_id: str,
    body: VersionRequest,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> StoryVersionResponse:
    project = require_project_owner(db, actor, project_id)
    _check_version(project, body.version)
    story = _current_story(db, project)
    if story.owner_approved_at is not None or not story.facts or not story.shots:
        raise HTTPException(status_code=409, detail="STORYBOARD_NOT_REVIEWABLE")
    now = utc_now()
    for fact in story.facts:
        fact.confirmed_at = now
    set_story_status(story, StoryVersionStatus.APPROVED)
    story.owner_approved_at = now
    for shot in story.shots:
        set_shot_status(shot, StoryboardShotStatus.FRAME_PENDING)
    _set_project_status(project, WeddingProjectStatus.FRAME_REVIEW)
    project.frame_approved_version = None
    _record_audit(db, project, actor, "storyboard.approved", "story_version", story.id)
    db.commit()
    db.refresh(story)
    return _story_response(story)


@router.post("/wedding-projects/{project_id}/frames/approve", response_model=StoryVersionResponse)
def approve_storyboard_frames(
    project_id: str,
    body: FrameApprovalRequest,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> StoryVersionResponse:
    project = require_project_owner(db, actor, project_id)
    _check_version(project, body.version)
    story = _current_story(db, project)
    if story.owner_approved_at is None or story.status != "approved":
        raise HTTPException(status_code=409, detail="STORYBOARD_APPROVAL_REQUIRED")
    shots = {shot.id: shot for shot in story.shots}
    assignments = {assignment.shot_id: assignment.asset_id for assignment in body.frames}
    if len(assignments) != len(body.frames) or set(assignments) != set(shots):
        raise HTTPException(status_code=422, detail="FRAME_SET_INCOMPLETE")
    assets = {
        asset.id: asset
        for asset in db.execute(
            select(WeddingAsset).where(
                WeddingAsset.project_id == project.id,
                WeddingAsset.id.in_(assignments.values()),
            )
        ).scalars()
    }
    for shot_id, asset_id in assignments.items():
        asset = assets.get(asset_id)
        if asset is None or asset.asset_type not in {
            WeddingAssetType.PHOTO,
            WeddingAssetType.STORYBOARD_FRAME,
            WeddingAssetType.GENERATED_FRAME,
        }:
            raise HTTPException(status_code=422, detail="FRAME_ASSET_INVALID")
        shot = shots[shot_id]
        if (
            asset.asset_type == WeddingAssetType.PHOTO
            and shot.person_keys
            and asset.person_key not in shot.person_keys
        ):
            raise HTTPException(status_code=422, detail="FRAME_ASSET_PERSON_SCOPE_INVALID")
        shot.frame_asset_id = asset_id
        set_shot_status(shot, StoryboardShotStatus.FRAME_APPROVED)
    project.frame_approved_version = project.current_version
    _set_project_status(project, WeddingProjectStatus.READY_TO_GENERATE)
    _record_audit(db, project, actor, "frames.approved", "story_version", story.id)
    db.commit()
    db.refresh(story)
    return _story_response(story)


@router.post("/participant-reviews/{shot_id}", response_model=ParticipantReviewResponse)
def create_participant_review(
    shot_id: str,
    body: ParticipantReviewRequest,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> ParticipantReviewResponse:
    shot = db.get(StoryboardShot, shot_id)
    if shot is None:
        raise HTTPException(status_code=404, detail="Storyboard shot not found")
    project = db.get(WeddingProject, shot.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Storyboard shot not found")
    _check_version(project, body.project_version)
    participant = _participant_for_actor(db, project, actor)
    if participant.person_key not in shot.person_keys:
        raise HTTPException(status_code=403, detail="PARTICIPANT_PERSON_SCOPE_REQUIRED")
    asset = db.get(WeddingAsset, body.asset_id)
    if asset is None or asset.project_id != project.id:
        raise HTTPException(status_code=404, detail="Wedding asset not found")
    belongs_to_shot = shot.frame_asset_id == asset.id or shot.selected_asset_id == asset.id
    if not belongs_to_shot:
        belongs_to_shot = db.execute(
            select(ShotCandidate).where(ShotCandidate.shot_id == shot.id, ShotCandidate.asset_id == asset.id)
        ).scalar_one_or_none() is not None
    if not belongs_to_shot:
        raise HTTPException(status_code=409, detail="REVIEW_ASSET_VERSION_MISMATCH")
    existing = db.execute(
        select(ParticipantReview).where(
            ParticipantReview.participant_id == participant.id,
            ParticipantReview.shot_id == shot.id,
            ParticipantReview.project_version == project.current_version,
            ParticipantReview.asset_id == asset.id,
        )
    ).scalar_one_or_none()
    now = utc_now()
    if existing is None:
        existing = ParticipantReview(
            participant_id=participant.id,
            project_id=project.id,
            person_key=participant.person_key,
            project_version=project.current_version,
            shot_id=shot.id,
            asset_id=asset.id,
            result=ParticipantReviewStatus(body.result),
            confirmed_at=now,
        )
        db.add(existing)
    else:
        existing.asset_id = asset.id
        set_review_status(existing, ParticipantReviewStatus(body.result))
        existing.confirmed_at = now
    _record_audit(db, project, actor, "identity_review.recorded", "shot", shot.id, {"result": body.result})
    db.commit()
    db.refresh(existing)
    return ParticipantReviewResponse(
        id=existing.id,
        person_key=existing.person_key,
        project_version=existing.project_version,
        shot_id=existing.shot_id,
        asset_id=existing.asset_id,
        result=existing.result,
        confirmed_at=existing.confirmed_at,
    )


@router.get("/wedding-projects/{project_id}/quote", response_model=WeddingQuoteResponse)
def create_wedding_quote(
    project_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> WeddingQuoteResponse:
    project = require_project_owner(db, actor, project_id)
    story = _check_job_prerequisites(db, project, project.current_version)
    available_fen = (
        project.budget_limit_fen
        - project.budget_delivery_reserve_fen
        - project.budget_spent_fen
        - project.budget_reserved_fen
    )
    if available_fen <= 0:
        raise HTTPException(status_code=409, detail="BUDGET_LIMIT_EXCEEDED")
    now = utc_now()
    quote = WeddingQuote(
        project_id=project.id,
        project_version=project.current_version,
        max_budget_fen=available_fen,
        price_version="wedding-mv-v1",
        issued_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db.add(quote)
    _record_audit(db, project, actor, "quote.created", "story_version", story.id)
    db.commit()
    db.refresh(quote)
    return WeddingQuoteResponse(
        id=quote.id,
        project_version=quote.project_version,
        max_budget_fen=quote.max_budget_fen,
        price_version=quote.price_version,
        issued_at=quote.issued_at,
        expires_at=quote.expires_at,
    )


def _create_generation_job(
    project: WeddingProject,
    body: GenerationJobCreate,
    idempotency_key: str,
    actor: WeddingActor,
    db: Session,
    target_shot: StoryboardShot | None = None,
) -> GenerationJobResponse:
    fingerprint = hashlib.sha256(
        f"{body.quote_id}:{body.version}:{target_shot.id if target_shot else 'all'}".encode("utf-8")
    ).hexdigest()
    existing = db.execute(
        select(GenerationJob).where(
            GenerationJob.project_id == project.id,
            GenerationJob.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_KEY_REUSED")
        return _job_response(existing)
    story = _check_job_prerequisites(db, project, body.version)
    quote = db.get(WeddingQuote, body.quote_id)
    if (
        quote is None
        or quote.project_id != project.id
        or quote.project_version != project.current_version
        or quote.consumed_at is not None
    ):
        raise HTTPException(status_code=409, detail="QUOTE_INVALID")
    expires_at = quote.expires_at if quote.expires_at.tzinfo else quote.expires_at.replace(tzinfo=utc_now().tzinfo)
    if expires_at <= utc_now():
        raise HTTPException(status_code=409, detail="QUOTE_EXPIRED")
    if project.status not in {WeddingProjectStatus.READY_TO_GENERATE, WeddingProjectStatus.PREVIEW_REVIEW}:
        raise HTTPException(status_code=409, detail="PROJECT_NOT_READY_TO_GENERATE")
    job = GenerationJob(
        project_id=project.id,
        story_version_id=story.id,
        quote_id=quote.id,
        target_shot_id=target_shot.id if target_shot else None,
        project_version=project.current_version,
        idempotency_key=idempotency_key,
        request_fingerprint=fingerprint,
        status=WeddingJobStatus.QUEUED,
        progress_stage="queued",
        completed_shots=0,
        total_shots=1 if target_shot is not None else len(story.shots),
        reserved_amount_fen=0,
        actual_cost_fen=0,
    )
    try:
        db.add(job)
        db.flush()
        reserve_generation_budget(
            db,
            project,
            job,
            reservation_key=f"generation:{idempotency_key}",
            amount_fen=quote.max_budget_fen,
        )
        quote.consumed_at = utc_now()
        _set_project_status(project, WeddingProjectStatus.GENERATING)
        _record_audit(db, project, actor, "generation.queued", "generation_job", job.id, {"shots": job.total_shots})
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.execute(
            select(GenerationJob).where(
                GenerationJob.project_id == project.id,
                GenerationJob.idempotency_key == idempotency_key,
            )
        ).scalar_one_or_none()
        if existing is None or existing.request_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="IDEMPOTENCY_KEY_REUSED")
        return _job_response(existing)
    db.refresh(job)
    return _job_response(job)


@router.post("/wedding-projects/{project_id}/generation-jobs", response_model=GenerationJobResponse, status_code=202)
def create_generation_job(
    project_id: str,
    body: GenerationJobCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> GenerationJobResponse:
    project = require_project_owner(db, actor, project_id)
    return _create_generation_job(project, body, idempotency_key, actor, db)


@router.get("/wedding-generation-jobs/{job_id}", response_model=GenerationJobResponse)
def read_generation_job(
    job_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> GenerationJobResponse:
    job = db.get(GenerationJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Generation job not found")
    require_project_owner(db, actor, job.project_id)
    return _job_response(job)


@router.post(
    "/wedding-projects/{project_id}/generation-jobs/{job_id}/cancel",
    response_model=GenerationJobCancelResponse,
)
def cancel_generation_job(
    project_id: str,
    job_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> GenerationJobCancelResponse:
    project = require_project_owner(db, actor, project_id)
    job = db.get(GenerationJob, job_id)
    if job is None or job.project_id != project.id:
        raise HTTPException(status_code=404, detail="Generation job not found")
    if job.status == WeddingJobStatus.QUEUED:
        set_job_status(job, WeddingJobStatus.CANCELLED)
        release_generation_budget(db, job)
        job.finished_at = utc_now()
        active_job = db.execute(
            select(GenerationJob.id).where(
                GenerationJob.project_id == project.id,
                GenerationJob.status.in_(
                    [WeddingJobStatus.QUEUED, WeddingJobStatus.PROCESSING, WeddingJobStatus.CANCEL_REQUESTED]
                ),
            )
        ).first()
        if active_job is None and project.status == WeddingProjectStatus.GENERATING:
            ready_render = db.execute(
                select(RenderVersion.id).where(
                    RenderVersion.project_id == project.id,
                    RenderVersion.project_version == project.current_version,
                    RenderVersion.status.in_([RenderVersionStatus.READY, RenderVersionStatus.APPROVED]),
                )
            ).first()
            _set_project_status(
                project,
                WeddingProjectStatus.PREVIEW_REVIEW if ready_render else WeddingProjectStatus.READY_TO_GENERATE,
            )
    elif job.status == WeddingJobStatus.PROCESSING:
        set_job_status(job, WeddingJobStatus.CANCEL_REQUESTED)
        job.cancel_requested_at = utc_now()
    db.commit()
    return GenerationJobCancelResponse(id=job.id, status=str(job.status))


@router.post("/wedding-projects/{project_id}/shots/{shot_id}/regenerate", response_model=GenerationJobResponse, status_code=202)
def regenerate_wedding_shot(
    project_id: str,
    shot_id: str,
    body: GenerationJobCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> GenerationJobResponse:
    project = require_project_owner(db, actor, project_id)
    shot = db.get(StoryboardShot, shot_id)
    if shot is None or shot.project_id != project.id:
        raise HTTPException(status_code=404, detail="Storyboard shot not found")
    current_story = _current_story(db, project)
    if shot.story_version_id != current_story.id or shot.project_version != project.current_version:
        raise HTTPException(status_code=409, detail="SHOT_VERSION_STALE")
    return _create_generation_job(project, body, idempotency_key, actor, db, target_shot=shot)


@router.post("/wedding-projects/{project_id}/shots/{shot_id}/select-candidate", response_model=StoryVersionResponse)
def select_wedding_shot_candidate(
    project_id: str,
    shot_id: str,
    body: CandidateSelectionRequest,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> StoryVersionResponse:
    project = require_project_owner(db, actor, project_id)
    _check_version(project, body.project_version)
    shot = db.get(StoryboardShot, shot_id)
    candidate = db.get(ShotCandidate, body.candidate_id)
    if shot is None or shot.project_id != project.id or candidate is None:
        raise HTTPException(status_code=404, detail="Shot candidate not found")
    if candidate.project_id != project.id or candidate.shot_id != shot.id:
        raise HTTPException(status_code=404, detail="Shot candidate not found")
    if candidate.review_result == "rejected":
        raise HTTPException(status_code=409, detail="REJECTED_CANDIDATE_CANNOT_BE_SELECTED")
    shot.selected_candidate_id = candidate.id
    shot.selected_asset_id = None
    set_shot_status(shot, StoryboardShotStatus.CANDIDATE_REVIEW)
    _record_audit(db, project, actor, "shot.candidate_selected", "shot_candidate", candidate.id)
    db.commit()
    return _story_response(_current_story(db, project))


@router.post("/wedding-projects/{project_id}/shots/{shot_id}/accept-fallback", response_model=StoryVersionResponse)
def accept_photo_fallback(
    project_id: str,
    shot_id: str,
    body: FallbackAcceptRequest,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> StoryVersionResponse:
    project = require_project_owner(db, actor, project_id)
    _check_version(project, body.project_version)
    shot = db.get(StoryboardShot, shot_id)
    asset = db.get(WeddingAsset, body.asset_id)
    if shot is None or shot.project_id != project.id or asset is None or asset.project_id != project.id:
        raise HTTPException(status_code=404, detail="Wedding shot or asset not found")
    if (
        asset.asset_type != WeddingAssetType.PHOTO
        or len(shot.person_keys) != 1
        or asset.person_key != shot.person_keys[0]
    ):
        raise HTTPException(status_code=422, detail="PHOTO_FALLBACK_ASSET_INVALID")
    shot.selected_asset_id = asset.id
    shot.selected_candidate_id = None
    set_shot_status(shot, StoryboardShotStatus.FALLBACK)
    _record_audit(db, project, actor, "shot.photo_fallback_accepted", "shot", shot.id, {"asset_id": asset.id})
    db.commit()
    return _story_response(_current_story(db, project))


@router.post("/wedding-projects/{project_id}/render", response_model=RenderVersionResponse, status_code=202)
def create_render_version(
    project_id: str,
    body: RenderCreateRequest,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> RenderVersionResponse:
    project = require_project_owner(db, actor, project_id)
    _check_version(project, body.version)
    validate_active_consents(db, project)
    story = _current_story(db, project)
    if story.status != "approved" or project.frame_approved_version != project.current_version:
        raise HTTPException(status_code=409, detail="STORYBOARD_OR_FRAME_APPROVAL_REQUIRED")
    shots = sorted(story.shots, key=lambda item: item.shot_index)
    if not shots or sum(shot.duration_ms for shot in shots) != project.target_duration_ms:
        raise HTTPException(status_code=409, detail="TIMELINE_DURATION_INVALID")
    selected_assets: list[str] = []
    active = {
        person.person_key: person
        for person in db.execute(
            select(WeddingParticipant).where(
                WeddingParticipant.project_id == project.id,
                WeddingParticipant.consent_status == "active",
            )
        ).scalars()
    }
    for shot in shots:
        asset_id = shot.selected_asset_id
        if shot.selected_candidate_id:
            candidate = db.get(ShotCandidate, shot.selected_candidate_id)
            if candidate is None or candidate.shot_id != shot.id:
                raise HTTPException(status_code=409, detail="SELECTED_CANDIDATE_MISSING")
            asset_id = candidate.asset_id
        if asset_id is None:
            raise HTTPException(status_code=409, detail="SHOT_SELECTION_REQUIRED")
        selected_asset = db.get(WeddingAsset, asset_id)
        if selected_asset is None or selected_asset.project_id != project.id:
            raise HTTPException(status_code=409, detail="SELECTED_ASSET_MISSING")
        for person_key in shot.person_keys:
            participant = active.get(person_key)
            review = db.execute(
                select(ParticipantReview).where(
                    ParticipantReview.participant_id == participant.id if participant else False,
                    ParticipantReview.project_id == project.id,
                    ParticipantReview.project_version == project.current_version,
                    ParticipantReview.shot_id == shot.id,
                    ParticipantReview.asset_id == selected_asset.id,
                    ParticipantReview.result == "approved",
                )
            ).scalar_one_or_none()
            if review is None:
                raise HTTPException(status_code=409, detail="IDENTITY_REVIEW_REQUIRED")
        selected_assets.append(selected_asset.id)
    prior = db.execute(
        select(RenderVersion).where(RenderVersion.project_id == project.id).order_by(RenderVersion.version.desc())
    ).scalars().first()
    version = prior.version + 1 if prior else 1
    timeline_hash = hashlib.sha256(
        f"{project.current_version}:{project.style}:{','.join(selected_assets)}".encode("utf-8")
    ).hexdigest()
    render = RenderVersion(
        project_id=project.id,
        project_version=project.current_version,
        version=version,
        status=RenderVersionStatus.QUEUED,
        timeline_hash=timeline_hash,
        input_asset_ids=selected_assets,
    )
    db.add(render)
    _record_audit(db, project, actor, "render.queued", "render_version", render.id)
    db.commit()
    db.refresh(render)
    return _render_response(render)


@router.get("/wedding-projects/{project_id}/renders", response_model=list[RenderVersionResponse])
def list_wedding_renders(
    project_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> list[RenderVersionResponse]:
    project = _readable_project(db, actor, project_id)
    renders = db.execute(
        select(RenderVersion).where(RenderVersion.project_id == project.id).order_by(RenderVersion.version.desc())
    ).scalars()
    responses: list[RenderVersionResponse] = []
    for render in renders:
        response = _render_response(
            render,
            participant=actor.kind == "participant",
            participant_person_key=actor.person_key,
        )
        if actor.kind == "participant" and render.preview_asset_id:
            preview = db.get(WeddingAsset, render.preview_asset_id)
            if preview is None or not _can_read_asset(db, preview, actor):
                response = response.model_copy(update={"preview_asset_id": None})
        responses.append(response)
    return responses


@router.get("/wedding-projects/{project_id}/renders/{render_id}", response_model=RenderVersionResponse)
def get_wedding_render(
    project_id: str,
    render_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> RenderVersionResponse:
    project = _readable_project(db, actor, project_id)
    render = db.get(RenderVersion, render_id)
    if render is None or render.project_id != project.id:
        raise HTTPException(status_code=404, detail="Wedding render not found")
    if actor.kind == "participant" and render.preview_asset_id:
        preview = db.get(WeddingAsset, render.preview_asset_id)
        if preview is None or not _can_read_asset(db, preview, actor):
            raise HTTPException(status_code=404, detail="Wedding render not found")
    return _render_response(
        render,
        participant=actor.kind == "participant",
        participant_person_key=actor.person_key,
    )


@router.get("/wedding-projects/{project_id}/preview")
def get_wedding_preview(
    project_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
    storage: WeddingPrivateStorage = Depends(private_storage),
) -> Response:
    project = db.get(WeddingProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Wedding project not found")
    if actor.kind == "owner":
        if actor.owner_id != project.owner_id:
            raise HTTPException(status_code=404, detail="Wedding project not found")
    elif actor.project_id != project.id:
        raise HTTPException(status_code=404, detail="Wedding project not found")
    render = db.execute(
        select(RenderVersion)
        .where(
            RenderVersion.project_id == project.id,
            RenderVersion.project_version == project.current_version,
            RenderVersion.status.in_(["ready", "approved"]),
        )
        .order_by(RenderVersion.version.desc())
    ).scalars().first()
    if render is None or render.preview_asset_id is None:
        raise HTTPException(status_code=409, detail="PREVIEW_NOT_READY")
    asset = db.get(WeddingAsset, render.preview_asset_id)
    if asset is None or not _can_read_asset(db, asset, actor):
        raise HTTPException(status_code=404, detail="Wedding preview not found")
    return Response(
        content=storage.read(asset.object_key),
        media_type=asset.mime,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/wedding-projects/{project_id}/render-reviews", response_model=ParticipantReviewResponse)
def create_render_review(
    project_id: str,
    body: ParticipantReviewRequest,
    render_version_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> ParticipantReviewResponse:
    project = db.get(WeddingProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Wedding project not found")
    participant = _participant_for_actor(db, project, actor)
    _check_version(project, body.project_version)
    render = db.get(RenderVersion, render_version_id)
    if (
        render is None
        or render.project_id != project.id
        or render.project_version != project.current_version
        or render.status != "ready"
        or render.preview_asset_id != body.asset_id
    ):
        raise HTTPException(status_code=409, detail="RENDER_REVIEW_ASSET_MISMATCH")
    now = utc_now()
    review = db.execute(
        select(ParticipantReview).where(
            ParticipantReview.participant_id == participant.id,
            ParticipantReview.render_version_id == render.id,
            ParticipantReview.project_version == project.current_version,
        )
    ).scalar_one_or_none()
    if review is None:
        review = ParticipantReview(
            participant_id=participant.id,
            project_id=project.id,
            person_key=participant.person_key,
            project_version=project.current_version,
            render_version_id=render.id,
            asset_id=body.asset_id,
            result=ParticipantReviewStatus(body.result),
            confirmed_at=now,
        )
        db.add(review)
    else:
        review.asset_id = body.asset_id
        set_review_status(review, ParticipantReviewStatus(body.result))
        review.confirmed_at = now
    _record_audit(db, project, actor, "render.identity_reviewed", "render_version", render.id, {"result": body.result})
    db.commit()
    db.refresh(review)
    return ParticipantReviewResponse(
        id=review.id,
        person_key=review.person_key,
        project_version=review.project_version,
        shot_id=None,
        asset_id=review.asset_id,
        result=review.result,
        confirmed_at=review.confirmed_at,
    )


@router.post("/wedding-projects/{project_id}/approve", response_model=RenderVersionResponse)
def approve_wedding_render(
    project_id: str,
    render_version_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> RenderVersionResponse:
    project = require_project_owner(db, actor, project_id)
    render = db.get(RenderVersion, render_version_id)
    if render is None or render.project_id != project.id or render.project_version != project.current_version:
        raise HTTPException(status_code=404, detail="Render version not found")
    if render.status != "ready" or render.preview_asset_id is None:
        raise HTTPException(status_code=409, detail="RENDER_NOT_READY")
    for participant in validate_active_consents(db, project):
        review = db.execute(
            select(ParticipantReview).where(
                ParticipantReview.participant_id == participant.id,
                ParticipantReview.render_version_id == render.id,
                ParticipantReview.project_version == project.current_version,
                ParticipantReview.asset_id == render.preview_asset_id,
                ParticipantReview.result == "approved",
            )
        ).scalar_one_or_none()
        if review is None:
            raise HTTPException(status_code=409, detail="FINAL_IDENTITY_REVIEW_REQUIRED")
    render.owner_approved_at = utc_now()
    set_render_status(render, RenderVersionStatus.APPROVED)
    _set_project_status(project, WeddingProjectStatus.READY_TO_EXPORT)
    _record_audit(db, project, actor, "render.owner_approved", "render_version", render.id)
    db.commit()
    db.refresh(render)
    return RenderVersionResponse(
        id=render.id,
        project_version=render.project_version,
        version=render.version,
        status=render.status,
        preview_asset_id=render.preview_asset_id,
        export_asset_id=render.export_asset_id,
        output_duration_ms=render.output_duration_ms,
        owner_approved_at=render.owner_approved_at,
        created_at=render.created_at,
    )


@router.post("/wedding-projects/{project_id}/export", response_model=ExportResponse)
def export_wedding_render(
    project_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> ExportResponse:
    project = require_project_owner(db, actor, project_id)
    validate_active_consents(db, project)
    render = db.execute(
        select(RenderVersion)
        .where(
            RenderVersion.project_id == project.id,
            RenderVersion.project_version == project.current_version,
            RenderVersion.status == "approved",
            RenderVersion.owner_approved_at.is_not(None),
        )
        .order_by(RenderVersion.version.desc())
    ).scalars().first()
    if render is None or render.export_asset_id is None:
        raise HTTPException(status_code=409, detail="EXPORT_NOT_READY")
    asset = db.get(WeddingAsset, render.export_asset_id)
    if asset is None or asset.asset_type != WeddingAssetType.FINAL_VIDEO:
        raise HTTPException(status_code=409, detail="EXPORT_ASSET_INVALID")
    return ExportResponse(
        render_version_id=render.id,
        asset_id=asset.id,
        mime=asset.mime,
        size_bytes=asset.size_bytes,
    )


@router.post("/wedding-projects/{project_id}/cancel")
def cancel_wedding_project(
    project_id: str,
    actor: WeddingActor = Depends(get_wedding_actor),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    project = require_project_owner(db, actor, project_id)
    now = utc_now()
    for job in db.execute(
        select(GenerationJob).where(
            GenerationJob.project_id == project.id,
            GenerationJob.status.in_([WeddingJobStatus.QUEUED, WeddingJobStatus.PROCESSING]),
        )
    ).scalars():
        if job.status == WeddingJobStatus.QUEUED:
            set_job_status(job, WeddingJobStatus.CANCELLED)
            release_generation_budget(db, job)
            job.finished_at = now
        else:
            set_job_status(job, WeddingJobStatus.CANCEL_REQUESTED)
            job.cancel_requested_at = now
    _set_project_status(project, WeddingProjectStatus.CANCELLED)
    _record_audit(db, project, actor, "project.cancelled", "project", project.id)
    db.commit()
    return {"project_id": project.id, "status": str(project.status)}
