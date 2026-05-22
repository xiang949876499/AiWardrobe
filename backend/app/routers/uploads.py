from io import BytesIO
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.ai import AiService, DetectedGarment
from app.config import Settings, get_settings
from app.database import get_db
from app.models import Garment, UploadSession, User
from app.schemas import UploadSessionResponse
from app.security import get_current_user
from app.storage import StorageService

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("/garment-photo", response_model=UploadSessionResponse, status_code=201)
async def upload_garment_photo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> UploadSession:
    data = await file.read()
    await file.seek(0)
    storage = StorageService(settings)
    original = await storage.save_upload(file)
    upload = UploadSession(
        user_id=current_user.id,
        original_image_url=original.url,
        original_image_key=original.key,
        status="extracting",
    )
    db.add(upload)
    db.flush()

    ai = AiService(settings)
    detections = await ai.detect_garments(
        filename=file.filename or "upload.jpg",
        content_type=file.content_type or "image/jpeg",
        image_bytes=data,
    )
    if not detections:
        fallback = await ai.analyze_garment(
            filename=file.filename or "upload.jpg",
            content_type=file.content_type or "image/jpeg",
            image_bytes=data,
        )
        detections = [DetectedGarment(category=fallback.category, crop_box=None)]

    upload.status = "tagging"
    suffix = Path(file.filename or "upload.jpg").suffix or ".jpg"
    for index, detection in enumerate(detections, start=1):
        crop_bytes = _crop_image_bytes(data, detection.crop_box)
        crop = storage.save_bytes(crop_bytes, suffix=suffix, prefix="garments/crops")
        analysis = await ai.analyze_garment(
            filename=f"{index}-{file.filename or 'upload.jpg'}",
            content_type=file.content_type or "image/jpeg",
            image_bytes=crop_bytes,
        )
        raw = dict(analysis.raw)
        raw["detected_category"] = detection.category
        raw["crop_box"] = detection.crop_box
        garment = Garment(
            user_id=current_user.id,
            source_upload_id=upload.id,
            image_url=crop.url,
            image_key=crop.key,
            thumbnail_url=crop.url,
            category=detection.category,
            colors=analysis.colors,
            style=analysis.style,
            material=analysis.material,
            season=analysis.season,
            fit=analysis.fit,
            tags=analysis.tags,
            crop_box=detection.crop_box,
            ai_result=raw,
            ai_confidence=analysis.confidence,
            status="pending_review",
            review_status="pending_review",
        )
        db.add(garment)

    upload.status = "pending_review"
    db.commit()
    db.refresh(upload)
    return upload


@router.get("/{upload_id}", response_model=UploadSessionResponse)
def get_upload_session(
    upload_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UploadSession:
    upload = db.get(UploadSession, upload_id)
    if upload is None or upload.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Upload session not found")
    return upload


def _crop_image_bytes(data: bytes, crop_box: dict[str, int] | None) -> bytes:
    if crop_box is None:
        return data
    try:
        from PIL import Image

        with Image.open(BytesIO(data)) as image:
            left = max(0, int(crop_box.get("x", 0)))
            top = max(0, int(crop_box.get("y", 0)))
            right = min(image.width, left + max(1, int(crop_box.get("width", image.width))))
            bottom = min(image.height, top + max(1, int(crop_box.get("height", image.height))))
            if right <= left or bottom <= top:
                return data
            cropped = image.crop((left, top, right, bottom))
            output = BytesIO()
            cropped.convert("RGB").save(output, format="JPEG", quality=92)
            return output.getvalue()
    except Exception:
        return data
