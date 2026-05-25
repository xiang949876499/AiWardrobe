from io import BytesIO
import logging
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.ai import AiService, DetectedGarment
from app.billing import BillingService
from app.comfyui import ComfyUIClient, ComfyUIError, ComfyUIOutput
from app.config import Settings, get_settings
from app.database import get_db
from app.models import Garment, UploadSession, User
from app.schemas import GarmentResponse, UploadSessionResponse
from app.security import get_current_user
from app.storage import StorageService

router = APIRouter(prefix="/uploads", tags=["uploads"])
logger = logging.getLogger(__name__)


@router.post("/plain-garment", response_model=GarmentResponse, status_code=201)
async def upload_plain_garment(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> Garment:
    stored = await StorageService(settings).save_upload(file)
    garment = Garment(
        user_id=current_user.id,
        image_url=stored.url,
        image_key=stored.key,
        thumbnail_url=stored.url,
        category="top",
        colors=[],
        style="",
        material="",
        season=[],
        fit="",
        tags=[],
        crop_box=None,
        ai_result={"source": "plain_upload", "message": "Manual review required"},
        ai_confidence=0.0,
        status="pending_review",
        review_status="pending_review",
    )
    db.add(garment)
    db.commit()
    db.refresh(garment)
    return garment


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
    BillingService().reserve_upload_recognition(current_user.id, upload.id)

    ai = AiService(settings)
    if settings.workflow_provider == "comfyui":
        try:
            outputs = await ComfyUIClient(settings).run_garment_recognition(
                filename=file.filename or "upload.jpg",
                content_type=file.content_type or "image/jpeg",
                image_bytes=data,
            )
            if outputs:
                upload.status = "tagging"
                await _create_garments_from_comfyui_outputs(db, upload, current_user, storage, ai, outputs)
                upload.status = "pending_review"
                db.commit()
                db.refresh(upload)
                return upload
            _fail_upload(db, upload, "ComfyUI workflow did not return garment images")
            raise HTTPException(status_code=502, detail="ComfyUI workflow did not return garment images")
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("ComfyUI garment workflow failed")
            _fail_upload(db, upload, f"ComfyUI workflow failed: {exc}")
            raise HTTPException(status_code=502, detail="ComfyUI workflow failed") from exc

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


async def _create_garments_from_comfyui_outputs(
    db: Session,
    upload: UploadSession,
    current_user: User,
    storage: StorageService,
    ai: AiService,
    outputs: list[ComfyUIOutput],
) -> None:
    for index, output in enumerate(outputs, start=1):
        suffix = Path(output.filename).suffix or ".png"
        content_type = mimetypes.guess_type(output.filename)[0] or "image/png"
        stored = storage.save_bytes(output.data, suffix=suffix, prefix="garments/crops")
        analysis = await ai.analyze_garment(
            filename=f"{index}-{output.filename}",
            content_type=content_type,
            image_bytes=output.data,
        )
        raw = dict(analysis.raw)
        raw["workflow_provider"] = "comfyui"
        raw["comfyui_filename"] = output.filename
        raw["comfyui_type"] = output.type
        raw["comfyui_subfolder"] = output.subfolder
        raw["detected_category"] = output.category
        raw["crop_box"] = None
        garment = Garment(
            user_id=current_user.id,
            source_upload_id=upload.id,
            image_url=stored.url,
            image_key=stored.key,
            thumbnail_url=stored.url,
            category=output.category,
            colors=analysis.colors,
            style=analysis.style,
            material=analysis.material,
            season=analysis.season,
            fit=analysis.fit,
            tags=analysis.tags,
            crop_box=None,
            ai_result=raw,
            ai_confidence=analysis.confidence,
            status="pending_review",
            review_status="pending_review",
        )
        db.add(garment)


def _fail_upload(db: Session, upload: UploadSession, message: str) -> None:
    upload.status = "failed"
    upload.error_message = message
    db.commit()
