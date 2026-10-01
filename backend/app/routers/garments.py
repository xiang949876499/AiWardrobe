from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from uuid import UUID, uuid5
import hashlib
import json

from app.asset_service import garment_response
from app.config import Settings, get_settings
from app.candidate_ownership import lock_ownership_user
from app.database import get_db
from app.decision_context import bump_revision
from app.legacy_tasks import compatibility_key, image_task, wait_for_tasks, require_completed, garment_result
from app.image_inputs import ImageInputError, prepare_image
from app.models import Garment, PurchaseCandidate, User, utc_now
from app.schemas import GarmentListResponse, GarmentResponse, GarmentUpdate
from app.security import get_current_user

router = APIRouter(prefix="/garments", tags=["garments"])


@router.post("/upload", response_model=GarmentResponse, status_code=201)
async def upload_garment(
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    task = await image_task(db, current_user, settings, file, "garment_single", idempotency_key)
    await wait_for_tasks(db, [task])
    require_completed([task])
    return garment_result(db, task, settings)


@router.post("/batch-upload", response_model=GarmentListResponse, status_code=201)
async def batch_upload_garments(
    files: list[UploadFile] = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> GarmentListResponse:
    if not files or len(files) > 20:
        raise HTTPException(422, detail={"code": "invalid_batch_size"})
    base_key = compatibility_key(current_user.id, "garment_batch", [], idempotency_key) if idempotency_key else None
    manifest = []
    for file in files:
        try:
            prepared = prepare_image(await file.read(), file.content_type)
        except ImageInputError as exc:
            raise HTTPException(exc.status_code, detail={"code": exc.code}) from exc
        manifest.append({"sha256": hashlib.sha256(prepared.data).hexdigest(), "filename": file.filename})
        await file.seek(0)
    batch_fingerprint = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
    tasks = []
    for index, file in enumerate(files):
        key = str(uuid5(UUID(base_key), str(index))) if base_key else None
        tasks.append(await image_task(db, current_user, settings, file, "garment_single", key,
                                      extra_parameters={"batch_fingerprint": batch_fingerprint, "batch_index": index}))
    await wait_for_tasks(db, tasks)
    require_completed(tasks)
    return GarmentListResponse(items=[garment_result(db, task, settings) for task in tasks])


@router.get("", response_model=GarmentListResponse)
def list_garments(
    category: str | None = None,
    tag: str | None = None,
    color: str | None = None,
    season: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> GarmentListResponse:
    query = select(Garment).where(Garment.user_id == current_user.id)
    if category:
        query = query.where(Garment.category == category)
    garments = list(db.execute(query.order_by(Garment.created_at.desc())).scalars())
    if tag:
        garments = [garment for garment in garments if tag in (garment.tags or [])]
    if color:
        garments = [garment for garment in garments if color in (garment.colors or [])]
    if season:
        garments = [garment for garment in garments if season in (garment.season or [])]
    return GarmentListResponse(items=[garment_response(garment, settings) for garment in garments])


@router.get("/{garment_id}", response_model=GarmentResponse)
def get_garment(
    garment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    garment = _get_owned_garment(db, current_user, garment_id)
    return garment_response(garment, settings)


@router.patch("/{garment_id}", response_model=GarmentResponse)
def update_garment(
    garment_id: str,
    body: GarmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    garment = _get_owned_garment(db, current_user, garment_id)
    updates = body.model_dump(exclude_unset=True)
    changed = any(getattr(garment, key) != value for key, value in updates.items())
    was_eligible = (
        garment.status == "ready"
        and garment.review_status == "confirmed"
        and garment.user_confirmed_at is not None
    )
    for key, value in updates.items():
        setattr(garment, key, value)
    if garment.status != "failed":
        changed = changed or not was_eligible
        garment.review_status = "confirmed"
        garment.status = "ready"
        garment.user_confirmed_at = utc_now()
        if garment.analysis_source == "legacy_unknown":
            garment.analysis_source = "manual"
    if changed:
        bump_revision(db, current_user.id, "wardrobe_revision")
    db.commit()
    db.refresh(garment)
    return garment_response(garment, settings)


@router.delete("/{garment_id}", status_code=204)
def delete_garment(
    garment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    lock_ownership_user(db, current_user.id)
    garment = db.execute(select(Garment).where(
        Garment.id == garment_id, Garment.user_id == current_user.id,
    ).with_for_update()).scalar_one_or_none()
    if garment is None:
        raise HTTPException(status_code=404, detail="Garment not found")
    was_eligible = (
        garment.status == "ready"
        and garment.review_status == "confirmed"
        and garment.user_confirmed_at is not None
    )
    # Preserve explicit pre-repair manual references too. This uses recorded IDs,
    # never image similarity or guessed legacy ownership.
    candidates = db.execute(select(PurchaseCandidate).where(
        PurchaseCandidate.user_id == current_user.id,
        or_(PurchaseCandidate.id == garment.source_candidate_id,
            and_(PurchaseCandidate.owned_garment_id == garment.id,
                 PurchaseCandidate.ownership_state == "linked")),
    ).order_by(PurchaseCandidate.id).with_for_update()).scalars()
    for candidate in candidates:
        candidate.ownership_state = "removed"
        candidate.owned_garment_id = garment.id
        candidate.revision += 1
        candidate.updated_at = utc_now()
    if was_eligible:
        bump_revision(db, current_user.id, "wardrobe_revision")
    db.delete(garment)
    db.commit()


def _get_owned_garment(db: Session, current_user: User, garment_id: str) -> Garment:
    garment = db.get(Garment, garment_id)
    if garment is None or garment.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Garment not found")
    return garment
