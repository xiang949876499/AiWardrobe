
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile, status
from sqlalchemy.orm import Session

from app.asset_service import garment_response
from app.candidate_ownership import save_owned_candidate
from app.legacy_tasks import compatibility_key, image_task, wait_for_tasks, require_completed, purchase_result
from app.task_service import create_task
from app.url_safety import UnsafeURL, validate_url_syntax
from app.config import Settings, get_settings
from app.database import get_db
from app.models import Garment, User
from app.schemas import GarmentResponse, PurchaseAnalyzeRequest, PurchaseCandidateResponse
from app.security import get_current_user

router = APIRouter(prefix="/purchase", tags=["purchase"])


@router.post("/analyze", response_model=PurchaseCandidateResponse, status_code=201)
async def analyze_product_url(
    body: PurchaseAnalyzeRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    try:
        validate_url_syntax(body.url)
    except UnsafeURL as exc:
        raise HTTPException(422, detail={"code": exc.code}) from exc
    parameters = {"url": body.url.strip()}
    key = compatibility_key(current_user.id, "purchase_url", parameters, idempotency_key, db=db)
    task = create_task(db, current_user, "purchase_url", parameters, key, queue_limit=settings.task_user_queue_limit)
    await wait_for_tasks(db, [task])
    require_completed([task])
    return purchase_result(db, task, settings)


@router.post("/analyze-image", response_model=PurchaseCandidateResponse, status_code=201)
async def analyze_uploaded_product_image(
    file: UploadFile = File(...),
    product_url: str = Form(""),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    task = await image_task(db, current_user, settings, file, "purchase_image", idempotency_key, product_url=product_url)
    await wait_for_tasks(db, [task])
    require_completed([task])
    return purchase_result(db, task, settings)


@router.post("/candidates/{candidate_id}/save", response_model=GarmentResponse, status_code=status.HTTP_201_CREATED)
def save_purchase_candidate(
    candidate_id: str,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> Garment:
    result = save_owned_candidate(db, current_user.id, candidate_id)
    response.status_code = 201 if result.created else 200
    return garment_response(result.garment, settings)
