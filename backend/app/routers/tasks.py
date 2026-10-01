from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.asset_service import create_image_asset
from app.config import Settings, get_settings
from app.database import get_db
from app.image_inputs import ImageInputError
from app.models import AnalysisTask, ProviderOperation, ShoppingRecommendationItem, User
from app.schemas import AnalysisTaskListResponse, AnalysisTaskResponse, PurchaseAnalyzeRequest
from app.security import get_current_user
from app.task_service import (
    TaskServiceError,
    advance_task_stage,
    create_task,
    encode_task_cursor,
    list_tasks,
    owned_task,
    task_response,
)
from app.url_safety import UnsafeURL, validate_url_syntax

router = APIRouter(prefix="/v2/tasks", tags=["analysis-tasks"])


class RecommendationTaskRequest(BaseModel):
    item_id: str = Field(min_length=1, max_length=36)


class EmptyTaskRequest(BaseModel):
    pass


@router.post("/purchase-analysis", response_model=AnalysisTaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_purchase_analysis_task(
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    if request.headers.get("content-type", "").lower().startswith("application/json"):
        try:
            body = PurchaseAnalyzeRequest.model_validate(await request.json())
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_purchase_request"}) from exc
        try:
            validate_url_syntax(body.url)
        except UnsafeURL as exc:
            raise HTTPException(status_code=422, detail={"code": exc.code}) from exc
        task = create_task(
            db,
            current_user,
            "purchase_url",
            {"url": body.url},
            idempotency_key,
            queue_limit=settings.task_user_queue_limit,
        )
    else:
        form = await request.form()
        upload = form.get("file")
        if not isinstance(upload, StarletteUploadFile) or not upload.filename:
            raise HTTPException(status_code=422, detail={"code": "image_required"})
        data = await upload.read()
        try:
            asset = create_image_asset(db, settings, current_user.id, data, upload.content_type)
        except ImageInputError as exc:
            raise HTTPException(status_code=exc.status_code, detail={"code": exc.code}) from exc
        product_url = str(form.get("product_url") or "").strip()
        task = create_task(
            db,
            current_user,
            "purchase_image",
            {
                "product_url": product_url,
                "input_sha256": asset.original.sha256,
                "content_type": asset.original.mime_type,
                "filename": Path(upload.filename).name,
            },
            idempotency_key,
            input_refs={"asset_id": asset.original.id},
            queue_limit=settings.task_user_queue_limit,
        )
    return task_response(db, task)


@router.post("/garment-upload", response_model=AnalysisTaskResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_garment_upload_task(
    file: UploadFile = File(...),
    mode: str = Form("single"),
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    if mode not in {"single", "split"}:
        raise HTTPException(status_code=422, detail={"code": "invalid_upload_mode"})
    data = await file.read()
    try:
        asset = create_image_asset(db, settings, current_user.id, data, file.content_type)
    except ImageInputError as exc:
        raise HTTPException(status_code=exc.status_code, detail={"code": exc.code}) from exc
    task = create_task(
        db,
        current_user,
        "garment_single" if mode == "single" else "garment_split",
        {
            "mode": mode,
            "input_sha256": asset.original.sha256,
            "content_type": asset.original.mime_type,
            "filename": Path(file.filename or "garment-image").name,
        },
        idempotency_key,
        input_refs={"asset_id": asset.original.id},
        queue_limit=settings.task_user_queue_limit,
    )
    return task_response(db, task)


@router.post("/recommendation-analysis", response_model=AnalysisTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def create_recommendation_analysis_task(
    body: RecommendationTaskRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    item = db.execute(
        select(ShoppingRecommendationItem).where(
            ShoppingRecommendationItem.id == body.item_id,
            ShoppingRecommendationItem.user_id == current_user.id,
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail={"code": "recommendation_item_not_found"})
    task = create_task(
        db,
        current_user,
        "recommendation_item",
        {"item_id": body.item_id},
        idempotency_key,
        input_refs={"recommendation_item_id": body.item_id},
        queue_limit=settings.task_user_queue_limit,
    )
    return task_response(db, task)


@router.get("", response_model=AnalysisTaskListResponse)
def get_tasks(
    status_filter: str | None = Query(default=None, alias="status"),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    if status_filter and status_filter not in {"queued", "running", "succeeded", "failed", "cancelled"}:
        raise HTTPException(status_code=422, detail={"code": "invalid_task_status"})
    tasks, has_more = list_tasks(
        db,
        current_user,
        status=status_filter,
        cursor=cursor,
        limit=limit,
    )
    return {
        "items": [task_response(db, task) for task in tasks],
        "next_cursor": encode_task_cursor(tasks[-1], current_user.id, status_filter) if has_more and tasks else None,
        "has_more": has_more,
    }


@router.get("/{task_id}", response_model=AnalysisTaskResponse)
def get_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    task = owned_task(db, current_user, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail={"code": "task_not_found"})
    return task_response(db, task)


@router.post("/{task_id}/retry", response_model=AnalysisTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_task(
    task_id: str,
    _body: EmptyTaskRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    task = db.execute(select(AnalysisTask).where(
        AnalysisTask.id == task_id, AnalysisTask.user_id == current_user.id,
    ).with_for_update()).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail={"code": "task_not_found"})
    try:
        retry_key = str(UUID(idempotency_key))
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_idempotency_key"}) from exc
    params = dict(task.input_parameters or {})
    replays = dict(params.get("retry_responses") or {})
    if retry_key in replays:
        return replays[retry_key]
    operations = list(db.execute(select(ProviderOperation).where(
        ProviderOperation.task_id == task.id,
    ).with_for_update()).scalars())
    if any(op.submission_status not in {"not_submitted", "failed", "completed"} for op in operations):
        raise HTTPException(status_code=409, detail={"code": "provider_submission_unknown"})
    if any(op.submission_status == "completed" and not op.result_data for op in operations):
        raise HTTPException(status_code=409, detail={"code": "provider_result_unavailable"})
    if task.status != "failed":
        raise HTTPException(status_code=409, detail={"code": "task_not_retryable"})
    if task.attempt_count >= 3:
        raise HTTPException(status_code=409, detail={"code": "task_retry_exhausted"})
    claimed = db.execute(update(AnalysisTask).where(
        AnalysisTask.id == task.id, AnalysisTask.status == "failed",
    ).values(status="queued", cancel_requested=False, lease_token=None,
             lease_expires_at=None, error_code=None, next_attempt_at=datetime.now(timezone.utc)))
    if claimed.rowcount != 1:
        db.rollback()
        raise HTTPException(status_code=409, detail={"code": "task_not_retryable"})
    db.refresh(task)
    advance_task_stage(task, "validating")
    db.flush()
    response = jsonable_encoder(task_response(db, task))
    replays[retry_key] = response
    params["retry_responses"] = replays
    task.input_parameters = params
    db.commit()
    return response


@router.post("/{task_id}/cancel", response_model=AnalysisTaskResponse)
def cancel_task(
    task_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    task = db.execute(select(AnalysisTask).where(
        AnalysisTask.id == task_id, AnalysisTask.user_id == current_user.id,
    ).with_for_update()).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail={"code": "task_not_found"})
    if task.status == "succeeded":
        raise HTTPException(status_code=409, detail={"code": "task_already_completed"})
    if task.status in {"queued", "running"}:
        task.cancel_requested = True
        task.status = "cancelled"
        unresolved = list(db.execute(select(ProviderOperation).where(
            ProviderOperation.task_id == task.id,
            ProviderOperation.submission_status.in_(["unknown", "submitting", "submitted"]),
        )).scalars())
        if unresolved:
            advance_task_stage(task, "awaiting_resolution")
        elif not task.lease_token:
            task.lease_expires_at = None
        db.commit()
        db.refresh(task)
    return task_response(db, task)


@router.post("/{task_id}/reconcile", response_model=AnalysisTaskResponse, status_code=status.HTTP_202_ACCEPTED)
def reconcile_task(
    task_id: str,
    _body: EmptyTaskRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    from app.provider_recovery import queryable, recovery_time
    from app.models import utc_now
    task = db.execute(select(AnalysisTask).where(
        AnalysisTask.id == task_id, AnalysisTask.user_id == current_user.id,
    ).with_for_update()).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail={"code": "task_not_found"})
    operations = list(db.execute(select(ProviderOperation).where(
        ProviderOperation.task_id == task.id,
        ProviderOperation.submission_status.in_(["unknown", "submitting", "submitted"]),
    ).with_for_update()).scalars())
    if not operations:
        raise HTTPException(status_code=409, detail={"code": "task_reconciliation_not_required"})
    if any(not queryable(op) for op in operations):
        raise HTTPException(status_code=409, detail={"code": "provider_reconciliation_unsupported", "retryable": False})
    now = utc_now()
    if task.lease_token and task.lease_expires_at and recovery_time(task.lease_expires_at) > now:
        raise HTTPException(status_code=409, detail={"code": "task_reconciliation_in_progress"})
    for op in operations:
        evidence = dict(op.reconciliation_evidence or {})
        stamps = [recovery_time(evidence[key]) for key in ("last_query_at", "reconcile_requested_at") if evidence.get(key)]
        last = max(stamps) if stamps else None
        if last and (now - recovery_time(last)).total_seconds() < 30:
            raise HTTPException(status_code=429, detail={"code": "reconciliation_rate_limited"}, headers={"Retry-After": "30"})
        evidence["reconcile_requested_at"] = now.isoformat()
        evidence["manual_query"] = True
        op.reconciliation_evidence = evidence
    task.status = "cancelled" if task.cancel_requested else "running"
    advance_task_stage(task, "awaiting_resolution")
    task.next_attempt_at = now
    task.error_code = None
    db.commit()
    return task_response(db, task)
