from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import AiService
from app.config import Settings, get_settings
from app.database import get_db
from app.models import Garment, PurchaseCandidate, User
from app.preferences import preference_context
from app.product_extraction import ProductExtractionError, extract_product_image
from app.purchase_analysis import analyze_purchase, explain_purchase
from app.schemas import GarmentResponse, PurchaseAnalyzeRequest, PurchaseCandidateResponse
from app.security import get_current_user
from app.storage import StorageService

router = APIRouter(prefix="/purchase", tags=["purchase"])


@router.post("/analyze", response_model=PurchaseCandidateResponse, status_code=201)
async def analyze_product_url(
    body: PurchaseAnalyzeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> PurchaseCandidate:
    try:
        extracted = await extract_product_image(body.url)
    except ProductExtractionError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from exc
    return await _create_candidate_from_image(
        db=db,
        current_user=current_user,
        settings=settings,
        product_url=extracted.product_url,
        source_image_url=extracted.source_image_url,
        image_bytes=extracted.image_bytes,
        content_type=extracted.content_type,
        title=extracted.title,
        domain=extracted.domain,
        price=extracted.price,
    )


@router.post("/analyze-image", response_model=PurchaseCandidateResponse, status_code=201)
async def analyze_uploaded_product_image(
    file: UploadFile = File(...),
    product_url: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> PurchaseCandidate:
    image_bytes = await file.read()
    source_url = file.filename or "manual-product-image"
    domain = urlparse(product_url).netloc.lower() if product_url else "manual-upload"
    return await _create_candidate_from_image(
        db=db,
        current_user=current_user,
        settings=settings,
        product_url=product_url or source_url,
        source_image_url=source_url,
        image_bytes=image_bytes,
        content_type=file.content_type or "image/jpeg",
        title=Path(file.filename or "Product image").stem,
        domain=domain,
        price=None,
    )


@router.post("/candidates/{candidate_id}/save", response_model=GarmentResponse, status_code=status.HTTP_201_CREATED)
def save_purchase_candidate(
    candidate_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Garment:
    candidate = _get_owned_candidate(db, current_user, candidate_id)
    if candidate.status == "saved":
        raise HTTPException(status_code=400, detail="Candidate already saved")
    garment = Garment(
        user_id=current_user.id,
        image_url=candidate.image_url,
        image_key=candidate.image_key,
        thumbnail_url=candidate.thumbnail_url,
        category=candidate.category,
        colors=candidate.colors,
        style=candidate.style,
        material=candidate.material,
        season=candidate.season,
        fit=candidate.fit,
        tags=candidate.tags,
        ai_result=candidate.ai_result,
        ai_confidence=candidate.ai_confidence,
        status="ready",
        review_status="confirmed",
    )
    candidate.status = "saved"
    db.add(garment)
    db.commit()
    db.refresh(garment)
    return garment


async def _create_candidate_from_image(
    db: Session,
    current_user: User,
    settings: Settings,
    product_url: str,
    source_image_url: str,
    image_bytes: bytes,
    content_type: str,
    title: str,
    domain: str,
    price: str | None,
) -> PurchaseCandidate:
    suffix = _suffix_for_image(content_type, source_image_url)
    stored = StorageService(settings).save_bytes(image_bytes, suffix=suffix, prefix="purchase")
    try:
        analysis = await AiService(settings).analyze_garment(
            filename=Path(source_image_url).name or "purchase-candidate.jpg",
            content_type=content_type,
            image_bytes=image_bytes,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="vl_analysis_failed") from exc

    ready_garments = list(
        db.execute(
            select(Garment).where(Garment.user_id == current_user.id, Garment.status == "ready")
        ).scalars()
    )
    decision = analyze_purchase(analysis, ready_garments, preference_context(db, current_user.id))
    if price:
        decision.analysis["product_price"] = {"current": price, "currency": "CNY", "source": "product_page"}
    reason_summary = await explain_purchase(settings, decision, analysis)
    candidate = PurchaseCandidate(
        user_id=current_user.id,
        product_url=product_url,
        source_image_url=source_image_url,
        image_url=stored.url,
        image_key=stored.key,
        thumbnail_url=stored.url,
        title=title,
        domain=domain,
        category=analysis.category,
        colors=analysis.colors,
        style=analysis.style,
        material=analysis.material,
        season=analysis.season,
        fit=analysis.fit,
        tags=analysis.tags,
        ai_result=analysis.raw,
        ai_confidence=analysis.confidence,
        similar_items=[item.__dict__ for item in decision.similar_items],
        recommendation=decision.recommendation,
        score=decision.score,
        reason_summary=reason_summary,
        analysis=decision.analysis,
        status="ready",
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate


def _get_owned_candidate(db: Session, current_user: User, candidate_id: str) -> PurchaseCandidate:
    candidate = db.get(PurchaseCandidate, candidate_id)
    if candidate is None or candidate.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Candidate not found")
    return candidate


def _suffix_for_image(content_type: str, source_image_url: str) -> str:
    suffix = Path(urlparse(source_image_url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return suffix
    return {
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
    }.get(content_type.lower(), ".jpg")
