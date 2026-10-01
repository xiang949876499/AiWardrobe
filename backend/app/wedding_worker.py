from __future__ import annotations

import argparse
import hashlib
import time
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings, get_settings
from app.database import get_session_factory
from app.models import (
    BudgetLedger,
    CandidateReviewStatus,
    GenerationJob,
    RenderVersion,
    RenderVersionStatus,
    ShotCandidate,
    StoryVersion,
    StoryboardShot,
    StoryboardShotStatus,
    WeddingAsset,
    WeddingAssetType,
    WeddingProjectStatus,
    WeddingAuditEvent,
    WeddingJobStatus,
    WeddingProject,
    utc_now,
)
from app.wedding_media import (
    FFmpegRenderer,
    MediaOutput,
    ProviderBundle,
    ProviderUnavailable,
    RenderInput,
    build_provider_bundle,
)
from app.wedding_service import (
    WeddingPrivateStorage,
    release_generation_budget,
    set_job_status,
    set_project_status,
    set_render_status,
    set_shot_status,
    settle_generation_budget,
    validate_active_consents,
)

# Re-export the request shape to keep worker clients on one import surface.
from app.wedding_media import VideoGenerationRequest


SessionFactory = sessionmaker[Session]


@dataclass(frozen=True)
class WorkerResult:
    work_id: str
    status: str
    error_code: str | None = None


def _job_shots(db: Session, job: GenerationJob) -> list[StoryboardShot]:
    statement = select(StoryboardShot).where(StoryboardShot.story_version_id == job.story_version_id)
    if job.target_shot_id is not None:
        statement = statement.where(StoryboardShot.id == job.target_shot_id)
    return list(db.execute(statement.order_by(StoryboardShot.shot_index)).scalars())


def claim_next_generation_job(session_factory: SessionFactory | None = None) -> str | None:
    """Atomically claim one queued job; the conditional update is the SQLite-safe CAS."""
    factory = session_factory or get_session_factory()
    _recover_stale_generation_jobs(factory)
    with factory() as db:
        candidate = db.execute(
            select(GenerationJob)
            .where(GenerationJob.status == WeddingJobStatus.QUEUED)
            .order_by(GenerationJob.created_at, GenerationJob.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).scalars().first()
        if candidate is None:
            return None
        now = utc_now()
        claim_stage = "recovered" if candidate.progress_stage == "recovered" else "claimed"
        set_job_status(candidate, WeddingJobStatus.PROCESSING)
        result = db.execute(
            update(GenerationJob)
            .where(
                GenerationJob.id == candidate.id,
                GenerationJob.status == WeddingJobStatus.QUEUED,
            )
            .values(
                status=WeddingJobStatus.PROCESSING,
                progress_stage=claim_stage,
                started_at=candidate.started_at or now,
                updated_at=now,
                error_code=None,
                error_category=None,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            db.rollback()
            return None
        db.expire(candidate)
        db.refresh(candidate)
        candidate.progress_stage = claim_stage
        candidate.started_at = candidate.started_at or now
        db.commit()
        return candidate.id


def _recover_stale_generation_jobs(
    session_factory: SessionFactory,
    *,
    now=None,
) -> list[str]:
    """Requeue stale claims via legal transitions and a status/lease compare-and-swap."""
    current_time = now or utc_now()
    stale_before = current_time - timedelta(seconds=get_settings().wedding_worker_stale_seconds)
    with session_factory() as db:
        stale_ids = list(
            db.execute(
                select(GenerationJob.id)
                .where(
                    GenerationJob.status == WeddingJobStatus.PROCESSING,
                    GenerationJob.updated_at < stale_before,
                )
                .order_by(GenerationJob.updated_at, GenerationJob.id)
            ).scalars()
        )

    recovered_ids: list[str] = []
    for job_id in stale_ids:
        with session_factory() as db:
            job = db.execute(
                select(GenerationJob)
                .where(
                    GenerationJob.id == job_id,
                    GenerationJob.status == WeddingJobStatus.PROCESSING,
                    GenerationJob.updated_at < stale_before,
                )
                .with_for_update(skip_locked=True)
            ).scalars().first()
            if job is None:
                continue
            # Validate both state transitions before the CAS makes recovery visible.
            set_job_status(job, WeddingJobStatus.NEEDS_ACTION)
            set_job_status(job, WeddingJobStatus.QUEUED)
            result = db.execute(
                update(GenerationJob)
                .where(
                    GenerationJob.id == job_id,
                    GenerationJob.status == WeddingJobStatus.PROCESSING,
                    GenerationJob.updated_at < stale_before,
                )
                .values(
                    status=WeddingJobStatus.QUEUED,
                    progress_stage="recovered",
                    updated_at=current_time,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                db.rollback()
                continue
            db.expire(job)
            db.refresh(job)
            db.commit()
            recovered_ids.append(job.id)
    return recovered_ids


def claim_next_render_version(session_factory: SessionFactory | None = None) -> str | None:
    factory = session_factory or get_session_factory()
    with factory() as db:
        candidate = db.execute(
            select(RenderVersion)
            .where(RenderVersion.status == RenderVersionStatus.QUEUED)
            .order_by(RenderVersion.created_at, RenderVersion.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        ).scalars().first()
        if candidate is None:
            return None
        set_render_status(candidate, RenderVersionStatus.PROCESSING)
        result = db.execute(
            update(RenderVersion)
            .where(
                RenderVersion.id == candidate.id,
                RenderVersion.status == RenderVersionStatus.QUEUED,
            )
            .values(status=RenderVersionStatus.PROCESSING)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            db.rollback()
            return None
        db.expire(candidate)
        db.refresh(candidate)
        db.commit()
        return candidate.id


def _status_result(job_id: str, factory: SessionFactory) -> WorkerResult:
    with factory() as db:
        job = db.get(GenerationJob, job_id)
        if job is None:
            return WorkerResult(job_id, "missing", "JOB_NOT_FOUND")
        return WorkerResult(job.id, str(job.status), job.error_code)


def _complete_or_release_reservation(db: Session, job: GenerationJob, *, successful: bool) -> bool:
    entry = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == job.id)).scalar_one_or_none()
    if entry is None:
        job.error_code = "BUDGET_RESERVATION_MISSING"
        return False
    if entry.status.value != "reserved":
        return True
    actual = int(job.actual_cost_fen or 0)
    if actual > entry.reserved_fen:
        # Keep the full reservation held for reconciliation; never disguise an overrun as a lower cost.
        job.error_code = "ACTUAL_COST_EXCEEDS_RESERVATION"
        job.error_category = "budget"
        job.progress_stage = "needs_action"
        return False
    if actual == 0 and not successful:
        release_generation_budget(db, job)
    else:
        settle_generation_budget(db, job, actual)
    return True


def _restore_project_workflow_state(db: Session, project: WeddingProject) -> None:
    if project.status != WeddingProjectStatus.GENERATING:
        return
    has_candidates = db.execute(
        select(ShotCandidate.id)
        .join(StoryboardShot, ShotCandidate.shot_id == StoryboardShot.id)
        .where(
            ShotCandidate.project_id == project.id,
            StoryboardShot.project_version == project.current_version,
        )
        .limit(1)
    ).first()
    has_render = db.execute(
        select(RenderVersion.id)
        .where(
            RenderVersion.project_id == project.id,
            RenderVersion.project_version == project.current_version,
            RenderVersion.status.in_([RenderVersionStatus.READY, RenderVersionStatus.APPROVED]),
        )
        .limit(1)
    ).first()
    next_status = (
        WeddingProjectStatus.PREVIEW_REVIEW
        if has_candidates is not None or has_render is not None
        else WeddingProjectStatus.READY_TO_GENERATE
    )
    set_project_status(project, next_status)


def _finalize_job(
    session_factory: SessionFactory,
    job_id: str,
    *,
    status: WeddingJobStatus,
    error_code: str | None,
    error_category: str | None = None,
    progress_stage: str | None = None,
) -> WorkerResult:
    with session_factory() as db:
        job = db.get(GenerationJob, job_id)
        if job is None:
            return WorkerResult(job_id, "missing", "JOB_NOT_FOUND")
        if job.status in {WeddingJobStatus.COMPLETED, WeddingJobStatus.FAILED, WeddingJobStatus.CANCELLED}:
            return WorkerResult(job.id, str(job.status), job.error_code)
        job.error_code = error_code
        job.error_category = error_category
        job.progress_stage = progress_stage or status.value
        _complete_or_release_reservation(db, job, successful=status == WeddingJobStatus.COMPLETED)
        if job.error_code == "ACTUAL_COST_EXCEEDS_RESERVATION":
            status = WeddingJobStatus.NEEDS_ACTION
        if job.status != status:
            set_job_status(job, status)
        job.finished_at = utc_now()
        project = db.get(WeddingProject, job.project_id)
        if project is not None:
            _restore_project_workflow_state(db, project)
        db.commit()
        return WorkerResult(job.id, str(job.status), job.error_code)


def _dispatch_preflight(
    session_factory: SessionFactory,
    job_id: str,
    shot_id: str,
) -> tuple[VideoGenerationRequest | None, str | None]:
    """Commit a truthful stage and re-check all gates at the provider dispatch boundary."""
    with session_factory() as db:
        job = db.get(GenerationJob, job_id)
        if job is None:
            return None, "JOB_NOT_FOUND"
        if job.status == WeddingJobStatus.CANCEL_REQUESTED or job.cancel_requested_at is not None:
            return None, "CANCEL_REQUESTED"
        if job.status != WeddingJobStatus.PROCESSING:
            return None, "JOB_NOT_PROCESSING"
        project = db.get(WeddingProject, job.project_id)
        if project is None:
            return None, "PROJECT_NOT_FOUND"
        if project.current_version != job.project_version:
            return None, "PROJECT_VERSION_STALE"
        if (
            project.budget_spent_fen
            + project.budget_reserved_fen
            + project.budget_delivery_reserve_fen
            > project.budget_limit_fen
        ):
            return None, "BUDGET_LIMIT_EXCEEDED"
        account_total_fen = db.scalar(
            select(
                func.coalesce(
                    func.sum(WeddingProject.budget_spent_fen + WeddingProject.budget_reserved_fen),
                    0,
                )
            ).where(WeddingProject.owner_id == project.owner_id)
        )
        if int(account_total_fen or 0) > get_settings().wedding_account_budget_limit_fen:
            return None, "ACCOUNT_BUDGET_LIMIT_EXCEEDED"
        ledger = db.execute(select(BudgetLedger).where(BudgetLedger.generation_job_id == job.id)).scalar_one_or_none()
        if ledger is None or ledger.status.value != "reserved" or ledger.reserved_fen != job.reserved_amount_fen:
            return None, "BUDGET_RESERVATION_INVALID"
        try:
            validate_active_consents(db, project)
        except HTTPException as exc:
            return None, str(exc.detail) if isinstance(exc.detail, str) else "CONSENT_REQUIRED"
        shot = db.get(StoryboardShot, shot_id)
        if (
            shot is None
            or shot.story_version_id != job.story_version_id
            or shot.project_id != project.id
            or shot.project_version != job.project_version
            or (job.target_shot_id is not None and shot.id != job.target_shot_id)
        ):
            return None, "SHOT_NOT_FOUND"
        if shot.shot_type == "photo":
            return None, "PHOTO_SHOT_DOES_NOT_DISPATCH"
        if shot.shot_type not in {"person_ai", "scene_ai"}:
            return None, "SHOT_TYPE_UNSUPPORTED"
        if shot.frame_asset_id is None or project.frame_approved_version != job.project_version:
            return None, "STATIC_FRAME_APPROVAL_REQUIRED"
        frame = db.get(WeddingAsset, shot.frame_asset_id)
        if frame is None or frame.project_id != project.id:
            return None, "STATIC_FRAME_MISSING"
        job.progress_stage = "dispatching_character_video" if shot.shot_type == "person_ai" else "dispatching_scene_video"
        db.commit()
        return (
            VideoGenerationRequest(
                project_id=project.id,
                job_id=job.id,
                shot_id=shot.id,
                shot_type=shot.shot_type,
                description=shot.description,
                subtitle=shot.subtitle,
                reference_image=b"",
                reference_mime=frame.mime,
                idempotency_key=f"{job.id}:{shot.id}",
            ),
            None,
        )


def process_claimed_generation_job(
    session_factory: SessionFactory,
    job_id: str,
    providers: ProviderBundle,
    storage: WeddingPrivateStorage,
) -> WorkerResult:
    with session_factory() as db:
        job = db.get(GenerationJob, job_id)
        if job is None:
            return WorkerResult(job_id, "missing", "JOB_NOT_FOUND")
        if job.status in {WeddingJobStatus.COMPLETED, WeddingJobStatus.FAILED, WeddingJobStatus.CANCELLED}:
            return WorkerResult(job.id, str(job.status), job.error_code)
        project = db.get(WeddingProject, job.project_id)
        if project is None:
            return _finalize_job(session_factory, job_id, status=WeddingJobStatus.FAILED, error_code="PROJECT_NOT_FOUND")
        if job.status == WeddingJobStatus.CANCEL_REQUESTED:
            return _finalize_job(session_factory, job_id, status=WeddingJobStatus.CANCELLED, error_code=None)
        if job.status != WeddingJobStatus.PROCESSING:
            return WorkerResult(job.id, str(job.status), "JOB_NOT_CLAIMED")
        if project.current_version != job.project_version:
            return _finalize_job(
                session_factory,
                job_id,
                status=WeddingJobStatus.FAILED,
                error_code="PROJECT_VERSION_STALE",
                error_category="version",
            )
        shots = _job_shots(db, job)
        job.total_shots = len(shots)
        shot_ids = [shot.id for shot in shots]
        db.commit()

    failure_codes: list[str] = []
    for shot_id in shot_ids:
        with session_factory() as db:
            job = db.get(GenerationJob, job_id)
            if job is None:
                return WorkerResult(job_id, "missing", "JOB_NOT_FOUND")
            shot = db.get(StoryboardShot, shot_id)
            project = db.get(WeddingProject, job.project_id)
            if shot is None or project is None:
                failure_codes.append("SHOT_NOT_FOUND")
                continue
            if job.status == WeddingJobStatus.CANCEL_REQUESTED or job.cancel_requested_at is not None:
                return _finalize_job(session_factory, job_id, status=WeddingJobStatus.CANCELLED, error_code=None)
            if project.current_version != job.project_version or shot.project_version != job.project_version:
                return _finalize_job(
                    session_factory,
                    job_id,
                    status=WeddingJobStatus.FAILED,
                    error_code="PROJECT_VERSION_STALE",
                    error_category="version",
                )
            existing = db.execute(
                select(ShotCandidate).where(
                    ShotCandidate.generation_job_id == job.id,
                    ShotCandidate.shot_id == shot.id,
                )
            ).scalars().first()
            if existing is not None:
                continue
            if shot.shot_type == "photo":
                job.completed_shots += 1
                job.progress_stage = "using_approved_photo"
                db.commit()
                continue
            if shot.status != StoryboardShotStatus.GENERATING:
                set_shot_status(shot, StoryboardShotStatus.GENERATING)
            db.commit()

        request, preflight_error = _dispatch_preflight(session_factory, job_id, shot_id)
        if request is None:
            if preflight_error == "PHOTO_SHOT_DOES_NOT_DISPATCH":
                continue
            if preflight_error == "CANCEL_REQUESTED":
                return _finalize_job(session_factory, job_id, status=WeddingJobStatus.CANCELLED, error_code=None)
            return _finalize_job(
                session_factory,
                job_id,
                status=WeddingJobStatus.FAILED,
                error_code=preflight_error or "PROVIDER_PREFLIGHT_FAILED",
                error_category="preflight",
            )

        # Consent, version, cancellation, and the complete project reservation were checked immediately before dispatch.
        with session_factory() as db:
            frame = db.get(WeddingAsset, db.get(StoryboardShot, shot_id).frame_asset_id)
            if frame is None:
                return _finalize_job(session_factory, job_id, status=WeddingJobStatus.FAILED, error_code="STATIC_FRAME_MISSING")
            try:
                reference_bytes = storage.read(frame.object_key)
            except OSError:
                return _finalize_job(
                    session_factory,
                    job_id,
                    status=WeddingJobStatus.FAILED,
                    error_code="STATIC_FRAME_BYTES_UNAVAILABLE",
                    error_category="storage",
                )
        request = VideoGenerationRequest(
            project_id=request.project_id,
            job_id=request.job_id,
            shot_id=request.shot_id,
            shot_type=request.shot_type,
            description=request.description,
            subtitle=request.subtitle,
            reference_image=reference_bytes,
            reference_mime=request.reference_mime,
            idempotency_key=request.idempotency_key,
        )

        provider = providers.character_video if request.shot_type == "person_ai" else providers.scene_video
        try:
            if provider is None:
                raise ProviderUnavailable("PROVIDER_UNAVAILABLE", "No provider is configured for this shot type")
            output = provider.generate(request)
        except ProviderUnavailable as exc:
            failure_codes.append(exc.code)
            _record_provider_failure(session_factory, job_id, shot_id, exc.code, exc.actual_cost_fen)
            continue
        except Exception as exc:
            actual = max(0, int(getattr(exc, "actual_cost_fen", 0) or 0))
            failure_codes.append("PROVIDER_ERROR")
            _record_provider_failure(session_factory, job_id, shot_id, "PROVIDER_ERROR", actual)
            continue

        result = _record_provider_output(session_factory, job_id, shot_id, output, storage)
        if result is not None:
            return result
        with session_factory() as db:
            persisted = db.execute(
                select(ShotCandidate.id).where(
                    ShotCandidate.generation_job_id == job_id,
                    ShotCandidate.shot_id == shot_id,
                )
            ).first()
            if persisted is None:
                job = db.get(GenerationJob, job_id)
                failure_codes.append(job.error_code if job and job.error_code else "PROVIDER_RESULT_INVALID")

    with session_factory() as db:
        job = db.get(GenerationJob, job_id)
        if job is None:
            return WorkerResult(job_id, "missing", "JOB_NOT_FOUND")
        if job.status == WeddingJobStatus.CANCEL_REQUESTED or job.cancel_requested_at is not None:
            return _finalize_job(session_factory, job_id, status=WeddingJobStatus.CANCELLED, error_code=None)
        if job.project.current_version != job.project_version:
            return _finalize_job(
                session_factory,
                job_id,
                status=WeddingJobStatus.FAILED,
                error_code="PROJECT_VERSION_STALE",
                error_category="version",
            )
        if failure_codes:
            failed_status = WeddingJobStatus.NEEDS_ACTION if job.completed_shots else WeddingJobStatus.FAILED
            return _finalize_job(
                session_factory,
                job_id,
                status=failed_status,
                error_code=failure_codes[0],
                error_category="provider",
                progress_stage="needs_action" if failed_status == WeddingJobStatus.NEEDS_ACTION else "failed",
            )
    return _finalize_job(
        session_factory,
        job_id,
        status=WeddingJobStatus.COMPLETED,
        error_code=None,
        progress_stage="review",
    )


def _record_provider_failure(
    session_factory: SessionFactory,
    job_id: str,
    shot_id: str,
    code: str,
    actual_cost_fen: int,
) -> None:
    with session_factory() as db:
        job = db.get(GenerationJob, job_id)
        shot = db.get(StoryboardShot, shot_id)
        if job is None:
            return
        job.actual_cost_fen += max(0, int(actual_cost_fen))
        job.error_code = code
        job.error_category = "provider"
        job.progress_stage = "shot_failed"
        if shot is not None and shot.status != StoryboardShotStatus.FAILED:
            try:
                set_shot_status(shot, StoryboardShotStatus.FAILED)
            except ValueError:
                pass
        db.commit()


def _record_provider_output(
    session_factory: SessionFactory,
    job_id: str,
    shot_id: str,
    output: MediaOutput,
    storage: WeddingPrivateStorage,
) -> WorkerResult | None:
    with session_factory() as db:
        job = db.get(GenerationJob, job_id)
        if job is None:
            return WorkerResult(job_id, "missing", "JOB_NOT_FOUND")
        job.actual_cost_fen += max(0, int(output.actual_cost_fen))
        job.provider_name = output.provider
        job.model_version = output.model_version
        if output.request_id:
            job.provider_request_id = output.request_id
        job.progress_stage = "provider_result_received"
        db.commit()

    # Provider cost is durable before we inspect late cancellation or version changes.
    with session_factory() as db:
        job = db.get(GenerationJob, job_id)
        shot = db.get(StoryboardShot, shot_id)
        project = db.get(WeddingProject, job.project_id) if job is not None else None
        if job is None or shot is None or project is None:
            return _finalize_job(session_factory, job_id, status=WeddingJobStatus.FAILED, error_code="SHOT_NOT_FOUND")
        if job.status == WeddingJobStatus.CANCEL_REQUESTED or job.cancel_requested_at is not None:
            return _finalize_job(session_factory, job_id, status=WeddingJobStatus.CANCELLED, error_code=None)
        if project.current_version != job.project_version or shot.project_version != job.project_version:
            return _finalize_job(
                session_factory,
                job_id,
                status=WeddingJobStatus.FAILED,
                error_code="PROJECT_VERSION_STALE",
                error_category="version",
            )
        try:
            validate_active_consents(db, project)
        except HTTPException:
            return _finalize_job(
                session_factory,
                job_id,
                status=WeddingJobStatus.CANCELLED,
                error_code="CONSENT_REQUIRED",
                error_category="consent",
            )
        if output.actual_cost_fen < 0 or not output.data or not output.mime or not output.provider or not output.model_version:
            _record_provider_failure(session_factory, job_id, shot_id, "PROVIDER_RESULT_INVALID", 0)
            return None
        if job.actual_cost_fen > job.reserved_amount_fen:
            return _finalize_job(
                session_factory,
                job_id,
                status=WeddingJobStatus.NEEDS_ACTION,
                error_code="ACTUAL_COST_EXCEEDS_RESERVATION",
                error_category="budget",
                progress_stage="needs_action",
            )
        if output.mime not in {"image/png", "image/jpeg", "image/webp", "video/mp4", "video/webm"}:
            _record_provider_failure(session_factory, job_id, shot_id, "PROVIDER_RESULT_UNSUPPORTED", 0)
            return None
        asset_type = WeddingAssetType.VIDEO_CANDIDATE if output.mime.startswith("video/") else WeddingAssetType.GENERATED_FRAME
        suffix = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4", "video/webm": ".webm"}[output.mime]
        object_key = None
        # The byte write and metadata insert are kept together; failed commits remove the orphan object.
        try:
            object_key = storage.put(project.id, output.data, suffix, output.mime)
            asset = WeddingAsset(
                project_id=project.id,
                asset_type=asset_type,
                object_key=object_key,
                sha256=hashlib.sha256(output.data).hexdigest(),
                mime=output.mime,
                size_bytes=len(output.data),
                generated=True,
                provider=output.provider,
            )
            db.add(asset)
            db.flush()
            candidate = ShotCandidate(
                project_id=project.id,
                shot_id=shot.id,
                generation_job_id=job.id,
                asset_id=asset.id,
                provider=output.provider,
                model_version=output.model_version,
                review_result=CandidateReviewStatus.PENDING,
                actual_cost_fen=output.actual_cost_fen,
            )
            db.add(candidate)
            set_shot_status(shot, StoryboardShotStatus.CANDIDATE_REVIEW)
            job.completed_shots += 1
            job.progress_stage = "candidate_review"
            db.commit()
        except Exception:
            db.rollback()
            if object_key:
                storage.delete(object_key)
            _record_provider_failure(session_factory, job_id, shot_id, "OUTPUT_PERSISTENCE_FAILED", 0)
            return None
    return None


def process_claimed_render_version(
    session_factory: SessionFactory,
    render_id: str,
    storage: WeddingPrivateStorage,
    renderer: FFmpegRenderer,
) -> WorkerResult:
    with session_factory() as db:
        render = db.get(RenderVersion, render_id)
        if render is None:
            return WorkerResult(render_id, "missing", "RENDER_NOT_FOUND")
        if render.status != RenderVersionStatus.PROCESSING:
            return WorkerResult(render.id, str(render.status), "RENDER_NOT_CLAIMED")
        project = db.get(WeddingProject, render.project_id)
        if project is None:
            return _fail_render(session_factory, render_id, "PROJECT_NOT_FOUND")
        if project.current_version != render.project_version:
            return _fail_render(session_factory, render_id, "PROJECT_VERSION_STALE")
        try:
            validate_active_consents(db, project)
        except HTTPException:
            return _fail_render(session_factory, render_id, "CONSENT_REQUIRED")
        story = db.execute(
            select(StoryVersion).where(
                StoryVersion.project_id == project.id,
                StoryVersion.version == render.project_version,
            )
        ).scalars().first()
        shots = list(
            db.execute(
                select(StoryboardShot)
                .where(StoryboardShot.story_version_id == story.id if story else False)
                .order_by(StoryboardShot.shot_index)
            ).scalars()
        )
        if not story or len(shots) != len(render.input_asset_ids) or not shots:
            return _fail_render(session_factory, render_id, "RENDER_INPUTS_STALE")
        render_inputs: list[RenderInput] = []
        assets: list[WeddingAsset] = []
        for shot, asset_id in zip(shots, render.input_asset_ids, strict=True):
            selected_asset_id = shot.selected_asset_id
            if shot.selected_candidate_id:
                candidate = db.get(ShotCandidate, shot.selected_candidate_id)
                if candidate is None or candidate.shot_id != shot.id:
                    return _fail_render(session_factory, render_id, "SELECTED_CANDIDATE_MISSING")
                selected_asset_id = candidate.asset_id
            if selected_asset_id != asset_id:
                return _fail_render(session_factory, render_id, "RENDER_INPUTS_STALE")
            asset = db.get(WeddingAsset, asset_id)
            if asset is None or asset.project_id != project.id:
                return _fail_render(session_factory, render_id, "RENDER_ASSET_MISSING")
            assets.append(asset)
            render_inputs.append(RenderInput(storage.read(asset.object_key), asset.mime, shot.subtitle, shot.duration_ms))
        render_version = render.project_version
        render_duration = sum(shot.duration_ms for shot in shots)
        has_demo_asset = any(asset.provider == "local_demo" for asset in assets)
        db.commit()

    demo = get_settings().wedding_media_mode.strip().lower() == "demo" or has_demo_asset
    try:
        # Preview is always watermarked and silent for local demo assets; export retains that label too.
        preview = renderer.render(render_inputs, demo=demo, resolution=(1280, 720))
        export = renderer.render(render_inputs, demo=demo, resolution=(1920, 1080))
    except ProviderUnavailable as exc:
        return _fail_render(session_factory, render_id, exc.code)

    stored_keys: list[str] = []
    try:
        with session_factory() as db:
            render = db.get(RenderVersion, render_id)
            project = db.get(WeddingProject, render.project_id) if render is not None else None
            if render is None or project is None:
                return _fail_render(session_factory, render_id, "RENDER_NOT_FOUND")
            if project.current_version != render_version or render.project_version != render_version:
                return _fail_render(session_factory, render_id, "PROJECT_VERSION_STALE")
            validate_active_consents(db, project)
            preview_key = storage.put(project.id, preview.data, ".mp4", "video/mp4")
            stored_keys.append(preview_key)
            export_key = storage.put(project.id, export.data, ".mp4", "video/mp4")
            stored_keys.append(export_key)
            preview_asset = WeddingAsset(
                project_id=project.id,
                asset_type=WeddingAssetType.PREVIEW_VIDEO,
                object_key=preview_key,
                sha256=hashlib.sha256(preview.data).hexdigest(),
                mime="video/mp4",
                size_bytes=len(preview.data),
                generated=True,
                provider="local_demo" if demo else "ffmpeg",
            )
            export_asset = WeddingAsset(
                project_id=project.id,
                asset_type=WeddingAssetType.FINAL_VIDEO,
                object_key=export_key,
                sha256=hashlib.sha256(export.data).hexdigest(),
                mime="video/mp4",
                size_bytes=len(export.data),
                generated=True,
                provider="local_demo" if demo else "ffmpeg",
            )
            db.add_all([preview_asset, export_asset])
            db.flush()
            render.preview_asset_id = preview_asset.id
            render.export_asset_id = export_asset.id
            render.output_duration_ms = render_duration
            set_render_status(render, RenderVersionStatus.READY)
            db.commit()
            return WorkerResult(render.id, str(render.status))
    except HTTPException:
        with session_factory() as db:
            project = db.get(WeddingProject, db.get(RenderVersion, render_id).project_id)
            version_changed = project is None or project.current_version != render_version
        return _fail_render(session_factory, render_id, "PROJECT_VERSION_STALE" if version_changed else "CONSENT_REQUIRED")
    except Exception:
        for key in stored_keys:
            storage.delete(key)
        return _fail_render(session_factory, render_id, "OUTPUT_PERSISTENCE_FAILED")


def _fail_render(session_factory: SessionFactory, render_id: str, error_code: str) -> WorkerResult:
    with session_factory() as db:
        render = db.get(RenderVersion, render_id)
        if render is None:
            return WorkerResult(render_id, "missing", "RENDER_NOT_FOUND")
        if render.status in {RenderVersionStatus.QUEUED, RenderVersionStatus.PROCESSING}:
            set_render_status(render, RenderVersionStatus.FAILED)
        db.add(
            WeddingAuditEvent(
                project_id=render.project_id,
                actor_kind="worker",
                actor_id=None,
                action="render.failed",
                target_type="render_version",
                target_id=render.id,
                project_version=render.project_version,
                metadata_json={"error_code": error_code},
            )
        )
        db.commit()
        return WorkerResult(render.id, str(render.status), error_code)


def process_next_generation_job(
    session_factory: SessionFactory | None = None,
    *,
    providers: ProviderBundle | None = None,
    storage: WeddingPrivateStorage | None = None,
) -> WorkerResult | None:
    factory = session_factory or get_session_factory()
    claimed = claim_next_generation_job(factory)
    if claimed is None:
        return None
    settings = get_settings()
    return process_claimed_generation_job(
        factory,
        claimed,
        providers or build_provider_bundle(settings),
        storage or WeddingPrivateStorage(settings),
    )


def process_next_render_version(
    session_factory: SessionFactory | None = None,
    *,
    storage: WeddingPrivateStorage | None = None,
    renderer: FFmpegRenderer | None = None,
) -> WorkerResult | None:
    factory = session_factory or get_session_factory()
    claimed = claim_next_render_version(factory)
    if claimed is None:
        return None
    settings = get_settings()
    return process_claimed_render_version(
        factory,
        claimed,
        storage or WeddingPrivateStorage(settings),
        renderer or FFmpegRenderer(settings),
    )


def run_worker_once() -> list[WorkerResult]:
    results: list[WorkerResult] = []
    generation = process_next_generation_job()
    if generation is not None:
        results.append(generation)
    render = process_next_render_version()
    if render is not None:
        results.append(render)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll durable wedding generation and render work")
    parser.add_argument("--once", action="store_true", help="Process at most one job and one render, then exit")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()
    while True:
        results = run_worker_once()
        for result in results:
            print(f"wedding worker {result.work_id}: {result.status} ({result.error_code or 'ok'})")
        if args.once:
            return
        if not results:
            time.sleep(max(0.1, args.poll_seconds))


if __name__ == "__main__":
    main()
