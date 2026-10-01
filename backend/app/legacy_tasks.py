"""Bounded legacy response adapters; provider execution belongs to TaskWorker."""
import asyncio
import json
import hashlib
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import HTTPException, UploadFile
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.asset_service import create_image_asset, garment_response, candidate_response, upload_session_response
from app.config import Settings
from app.image_inputs import ImageInputError
from app.models import AnalysisTask, Garment, ProviderOperation, PurchaseCandidate, UploadSession, User
from app.task_service import create_task

SYNC_WAIT_SECONDS = 20.0


def compatibility_key(user_id: str, operation: str, parameters: object, supplied: str | None,
                      *, db: Session | None = None) -> str:
    if supplied is not None:
        try:
            return str(UUID(supplied))
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, detail={"code": "invalid_idempotency_key"}) from exc
    if db is not None:
        # Serialize keyless lookup with task creation so requests spanning UTC
        # midnight cannot both mint different keys before either task commits.
        db.execute(select(User.id).where(User.id == user_id).with_for_update()).scalar_one()
        fingerprint = hashlib.sha256(json.dumps({"kind": operation, "parameters": parameters}, sort_keys=True).encode()).hexdigest()
        ambiguous = select(ProviderOperation.id).where(
            ProviderOperation.task_id == AnalysisTask.id,
            ProviderOperation.submission_status.in_(["unknown", "submitting", "submitted"]),
        ).exists()
        existing = db.scalar(select(AnalysisTask).where(
            AnalysisTask.user_id == user_id, AnalysisTask.kind == operation,
            AnalysisTask.input_fingerprint == fingerprint,
            or_(AnalysisTask.created_at >= datetime.now(timezone.utc) - timedelta(hours=24),
                AnalysisTask.status.in_(["queued", "running"]), ambiguous),
        ).order_by(AnalysisTask.created_at.desc()).limit(1))
        if existing is not None:
            return existing.idempotency_key
    # Deterministic generation protects simultaneous first submissions. Existing
    # identical requests use a rolling window, including across UTC midnight;
    # unresolved provider submissions are retained regardless of their age.
    day = datetime.now(timezone.utc).date().isoformat()
    canonical = json.dumps([user_id, operation, parameters, day], sort_keys=True, separators=(",", ":"))
    return str(uuid5(NAMESPACE_URL, canonical))


async def image_task(db: Session, user: User, settings: Settings, file: UploadFile,
                     kind: str, key: str | None, *, product_url: str = "", plain: bool = False,
                     extra_parameters: dict[str, object] | None = None) -> AnalysisTask:
    try:
        asset = create_image_asset(db, settings, user.id, await file.read(), file.content_type)
    except ImageInputError as exc:
        raise HTTPException(exc.status_code, detail={"code": exc.code}) from exc
    parameters = {
        "input_sha256": asset.original.sha256,
        "content_type": asset.original.mime_type,
        "filename": Path(file.filename or "garment-image").name,
    }
    if kind == "purchase_image":
        parameters["product_url"] = product_url.strip()
    else:
        parameters["mode"] = "split" if kind == "garment_split" else "single"
    if plain:
        parameters["ingestion_source"] = "plain_upload"
    if extra_parameters:
        parameters.update(extra_parameters)
    normalized_key = compatibility_key(user.id, kind, parameters, key, db=db)
    return create_task(db, user, kind, parameters, normalized_key,
                       input_refs={"asset_id": asset.original.id}, queue_limit=settings.task_user_queue_limit)


async def wait_for_tasks(db: Session, tasks: list[AnalysisTask]) -> list[AnalysisTask]:
    deadline = time.monotonic() + min(20.0, max(0.0, SYNC_WAIT_SECONDS))
    while True:
        # End each read transaction so a PostgreSQL worker's committed result is
        # visible on the next poll, without retaining locks during the wait.
        db.rollback()
        for task in tasks:
            db.refresh(task)
        if all(task.status in {"succeeded", "failed", "cancelled"} for task in tasks):
            return tasks
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return tasks
        await asyncio.sleep(min(0.1, remaining))


def require_completed(tasks: list[AnalysisTask]) -> None:
    pending = [task for task in tasks if task.status not in {"succeeded", "failed", "cancelled"}]
    if pending:
        detail = {"code": "analysis_pending", "task_id": pending[0].id}
        if len(tasks) > 1:
            detail["task_ids"] = [task.id for task in tasks]
        raise HTTPException(503, detail=detail, headers={"Retry-After": "2"})
    failed = next((task for task in tasks if task.status != "succeeded"), None)
    if failed:
        raise HTTPException(502, detail={"code": failed.error_code or "analysis_cancelled", "task_id": failed.id})


def garment_result(db: Session, task: AnalysisTask, settings: Settings) -> dict[str, object]:
    garment = _result(db, task, "garment", Garment)
    if task.input_parameters.get("ingestion_source") == "plain_upload":
        raw = dict(garment.ai_result or {})
        raw.update(source="plain_upload", ingestion_source="plain_upload", workflow_provider=None,
                   analysis_source=garment.analysis_source, provider=garment.provider, model_version=garment.model_version)
        garment.ai_result = raw
        db.commit()
    return garment_response(garment, settings)


def purchase_result(db: Session, task: AnalysisTask, settings: Settings) -> dict[str, object]:
    candidate = _result(db, task, "purchase_candidate", PurchaseCandidate)
    if candidate.decision_score is None:
        raise HTTPException(409, detail={"code": "evidence_insufficient", "task_id": task.id,
                                         "candidate_id": candidate.id})
    return candidate_response(db, candidate, settings)


def split_result(db: Session, task: AnalysisTask, settings: Settings) -> dict[str, object]:
    # Materialize only the old envelope. Splitting/tagging/charging were performed
    # by the worker; repeated compatibility reads reuse this same envelope.
    task = db.scalar(select(AnalysisTask).where(AnalysisTask.id == task.id).with_for_update())
    refs = dict(task.input_refs or {})
    upload = db.get(UploadSession, refs.get("legacy_upload_id")) if refs.get("legacy_upload_id") else None
    if upload is None:
        upload = UploadSession(user_id=task.user_id, original_image_url="", original_image_key="",
                               asset_id=refs.get("asset_id"), status="ready")
        db.add(upload)
        db.flush()
        for ref in task.result_refs or []:
            if ref.get("resource_type") == "garment":
                garment = db.get(Garment, ref["id"])
                if garment is not None and garment.user_id == task.user_id:
                    garment.source_upload_id = upload.id
        refs["legacy_upload_id"] = upload.id
        task.input_refs = refs
        db.commit()
        db.refresh(upload)
    return upload_session_response(upload, settings)


def _result(db, task, resource_type, model):
    ref = next((item for item in task.result_refs or [] if item.get("resource_type") == resource_type), None)
    resource = db.get(model, ref["id"]) if ref else None
    if resource is None or resource.user_id != task.user_id:
        raise HTTPException(409, detail={"code": "analysis_result_removed", "task_id": task.id})
    return resource
