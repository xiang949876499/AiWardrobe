from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import AiService
from app.config import Settings, get_settings
from app.database import get_db
from app.models import Garment, User
from app.schemas import GarmentListResponse, GarmentResponse, GarmentUpdate
from app.security import get_current_user
from app.storage import StorageService

router = APIRouter(prefix="/garments", tags=["garments"])


@router.post("/upload", response_model=GarmentResponse, status_code=201)
async def upload_garment(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> Garment:
    data = await file.read()
    await file.seek(0)
    stored = await StorageService(settings).save_upload(file)
    analysis = await AiService(settings).analyze_garment(
        filename=file.filename or "upload.jpg",
        content_type=file.content_type or "image/jpeg",
        image_bytes=data,
    )
    garment = Garment(
        user_id=current_user.id,
        image_url=stored.url,
        image_key=stored.key,
        thumbnail_url=stored.url,
        category=analysis.category,
        colors=analysis.colors,
        style=analysis.style,
        material=analysis.material,
        season=analysis.season,
        fit=analysis.fit,
        tags=analysis.tags,
        ai_result=analysis.raw,
        ai_confidence=analysis.confidence,
        status="ready",
        review_status="confirmed",
    )
    db.add(garment)
    db.commit()
    db.refresh(garment)
    return garment


@router.post("/batch-upload", response_model=GarmentListResponse, status_code=201)
async def batch_upload_garments(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> GarmentListResponse:
    items: list[Garment] = []
    for file in files:
        data = await file.read()
        await file.seek(0)
        stored = await StorageService(settings).save_upload(file)
        analysis = await AiService(settings).analyze_garment(
            filename=file.filename or "upload.jpg",
            content_type=file.content_type or "image/jpeg",
            image_bytes=data,
        )
        garment = Garment(
            user_id=current_user.id,
            image_url=stored.url,
            image_key=stored.key,
            thumbnail_url=stored.url,
            category=analysis.category,
            colors=analysis.colors,
            style=analysis.style,
            material=analysis.material,
            season=analysis.season,
            fit=analysis.fit,
            tags=analysis.tags,
            ai_result=analysis.raw,
            ai_confidence=analysis.confidence,
            status="ready",
            review_status="confirmed",
        )
        db.add(garment)
        items.append(garment)
    db.commit()
    for garment in items:
        db.refresh(garment)
    return GarmentListResponse(items=items)


@router.get("", response_model=GarmentListResponse)
def list_garments(
    category: str | None = None,
    tag: str | None = None,
    color: str | None = None,
    season: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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
    return GarmentListResponse(items=garments)


@router.get("/{garment_id}", response_model=GarmentResponse)
def get_garment(
    garment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Garment:
    garment = _get_owned_garment(db, current_user, garment_id)
    return garment


@router.patch("/{garment_id}", response_model=GarmentResponse)
def update_garment(
    garment_id: str,
    body: GarmentUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Garment:
    garment = _get_owned_garment(db, current_user, garment_id)
    updates = body.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(garment, key, value)
    if garment.status != "failed":
        garment.review_status = "confirmed"
        garment.status = "ready"
    db.commit()
    db.refresh(garment)
    return garment


@router.delete("/{garment_id}", status_code=204)
def delete_garment(
    garment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    garment = _get_owned_garment(db, current_user, garment_id)
    db.delete(garment)
    db.commit()


def _get_owned_garment(db: Session, current_user: User, garment_id: str) -> Garment:
    garment = db.get(Garment, garment_id)
    if garment is None or garment.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Garment not found")
    return garment
