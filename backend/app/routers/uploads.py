from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.asset_service import upload_session_response
from app.config import Settings, get_settings
from app.database import get_db
from app.legacy_tasks import image_task, wait_for_tasks, require_completed, garment_result, split_result
from app.models import UploadSession, User
from app.schemas import GarmentResponse, UploadSessionResponse
from app.security import get_current_user

router = APIRouter(prefix="/uploads", tags=["uploads"])


@router.post("/plain-garment", response_model=GarmentResponse, status_code=201)
async def upload_plain_garment(
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    task = await image_task(db, current_user, settings, file, "garment_single", idempotency_key, plain=True)
    await wait_for_tasks(db, [task])
    require_completed([task])
    return garment_result(db, task, settings)


@router.post("/garment-photo", response_model=UploadSessionResponse, status_code=201)
async def upload_garment_photo(
    file: UploadFile = File(...),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    task = await image_task(db, current_user, settings, file, "garment_split", idempotency_key)
    await wait_for_tasks(db, [task])
    require_completed([task])
    return split_result(db, task, settings)


@router.get("/{upload_id}", response_model=UploadSessionResponse)
def get_upload_session(
    upload_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    upload = db.get(UploadSession, upload_id)
    if upload is None or upload.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Upload session not found")
    return upload_session_response(upload, settings)
