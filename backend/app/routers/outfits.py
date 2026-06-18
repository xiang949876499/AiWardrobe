from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import AiService
from app.config import Settings, get_settings
from app.database import get_db
from app.models import Garment, Outfit, User
from app.schemas import (
    FavoriteUpdate,
    FixedUpdate,
    ManualOutfitRequest,
    OutfitGenerateRequest,
    OutfitListResponse,
    OutfitResponse,
)
from app.security import get_current_user
from app.season import current_season

router = APIRouter(prefix="/outfits", tags=["outfits"])


@router.post("/generate", response_model=OutfitResponse, status_code=201)
async def generate_outfit(
    body: OutfitGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> Outfit:
    garments = list(
        db.execute(
            select(Garment)
            .where(Garment.user_id == current_user.id, Garment.status == "ready")
            .order_by(Garment.created_at.desc())
        ).scalars()
    )
    categories = {garment.category for garment in garments}
    if len(garments) < 3 or not {"top", "bottom", "shoes"}.issubset(categories):
        raise HTTPException(status_code=400, detail="Not enough ready garments to generate an outfit")

    season = body.season or current_season(settings)
    items, explanation = await AiService(settings).generate_outfit(
        garments=garments,
        occasion=body.occasion,
        season=season,
        temperature=body.temperature,
        weather=body.weather,
    )
    items = _trusted_outfit_items(items, garments)
    outfit = Outfit(
        user_id=current_user.id,
        occasion=body.occasion,
        season=season,
        temperature=body.temperature,
        items=items,
        explanation=explanation,
        source="ai",
        weather_snapshot=body.weather,
    )
    db.add(outfit)
    db.commit()
    db.refresh(outfit)
    return outfit


@router.post("/manual", response_model=OutfitResponse, status_code=201)
def create_manual_outfit(
    body: ManualOutfitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Outfit:
    garments = list(
        db.execute(
            select(Garment).where(
                Garment.user_id == current_user.id,
                Garment.status == "ready",
                Garment.id.in_(body.garment_ids),
            )
        ).scalars()
    )
    found_ids = {garment.id for garment in garments}
    missing_ids = set(body.garment_ids) - found_ids
    if missing_ids:
        raise HTTPException(status_code=404, detail="Some garments are not ready or do not exist")

    garment_by_id = {garment.id: garment for garment in garments}
    ordered = [garment_by_id[garment_id] for garment_id in body.garment_ids]
    items = [
        {
            "garment_id": garment.id,
            "category": garment.category,
            "image_url": garment.image_url,
            "reason": "用户手动选择",
        }
        for garment in ordered
    ]
    season = body.season or current_season(get_settings())
    outfit = Outfit(
        user_id=current_user.id,
        name=body.name,
        occasion=body.occasion,
        season=season,
        temperature=body.temperature,
        items=items,
        explanation="用户保存的固定搭配" if body.is_fixed else "用户自定义搭配",
        source="manual",
        is_fixed=body.is_fixed,
        weather_snapshot=body.weather,
    )
    db.add(outfit)
    db.commit()
    db.refresh(outfit)
    return outfit


@router.get("/history", response_model=OutfitListResponse)
def list_outfits(
    favorite: bool | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OutfitListResponse:
    query = select(Outfit).where(Outfit.user_id == current_user.id)
    if favorite is not None:
        query = query.where(Outfit.is_favorite == favorite)
    outfits = list(db.execute(query.order_by(Outfit.created_at.desc())).scalars())
    return OutfitListResponse(items=outfits)


@router.patch("/{outfit_id}/favorite", response_model=OutfitResponse)
def update_favorite(
    outfit_id: str,
    body: FavoriteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Outfit:
    outfit = db.get(Outfit, outfit_id)
    if outfit is None or outfit.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Outfit not found")
    outfit.is_favorite = body.is_favorite
    db.commit()
    db.refresh(outfit)
    return outfit


@router.patch("/{outfit_id}/fixed", response_model=OutfitResponse)
def update_fixed(
    outfit_id: str,
    body: FixedUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Outfit:
    outfit = db.get(Outfit, outfit_id)
    if outfit is None or outfit.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Outfit not found")
    outfit.is_fixed = body.is_fixed
    db.commit()
    db.refresh(outfit)
    return outfit


@router.delete("/{outfit_id}", status_code=204)
def delete_outfit(
    outfit_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    outfit = db.get(Outfit, outfit_id)
    if outfit is None or outfit.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Outfit not found")
    db.delete(outfit)
    db.commit()
    return Response(status_code=204)


def _trusted_outfit_items(items: list[dict[str, object]], garments: list[Garment]) -> list[dict[str, object]]:
    garments_by_id = {garment.id: garment for garment in garments}
    trusted: list[dict[str, object]] = []
    for item in items:
        garment = garments_by_id.get(str(item.get("garment_id") or ""))
        if garment is None:
            continue
        trusted.append(
            {
                "garment_id": garment.id,
                "category": garment.category,
                "image_url": garment.image_url,
                "reason": str(item.get("reason") or "AI 推荐"),
            }
        )
    return trusted
