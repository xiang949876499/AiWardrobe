from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ai import AiService
from app.asset_access import asset_content_url
from app.config import Settings, get_settings
from app.database import get_db
from app.feedback_service import active_excluded_garments
from app.models import Garment, Outfit, User
from app.preferences import preference_context
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
) -> dict[str, object]:
    confirmed_garments = list(
        db.execute(
            select(Garment)
            .where(
                Garment.user_id == current_user.id,
                Garment.status == "ready",
                Garment.review_status == "confirmed",
                Garment.user_confirmed_at.is_not(None),
            )
            .order_by(Garment.created_at.desc())
        ).scalars()
    )
    preferences = preference_context(db, current_user.id) or {}
    excluded_ids = active_excluded_garments(db, current_user.id)
    avoided_categories = set(preferences.get("avoid_categories") or [])
    target_garment = _target_garment(body.garment_id, confirmed_garments)
    if target_garment and target_garment.id in excluded_ids:
        raise HTTPException(status_code=409, detail={"code": "target_garment_excluded"})
    if target_garment and target_garment.category in avoided_categories:
        raise HTTPException(status_code=409, detail={"code": "target_category_avoided"})
    garments = [
        garment for garment in confirmed_garments
        if garment.id not in excluded_ids and garment.category not in avoided_categories
    ]
    if target_garment:
        garments = [
            garment for garment in garments
            if garment.category != target_garment.category or garment.id == target_garment.id
        ]
        garments = [target_garment, *[garment for garment in garments if garment.id != target_garment.id]]
    categories = {garment.category for garment in garments}
    if not {"top", "bottom", "shoes"}.issubset(categories):
        raise HTTPException(status_code=409, detail={"code": "no_valid_combination"})
    if target_garment:
        target_seasons = {value.casefold() for value in target_garment.season if value.casefold() != "unknown"}
        if target_seasons:
            garments = [
                garment for garment in garments
                if garment.id == target_garment.id
                or not {value.casefold() for value in garment.season if value.casefold() != "unknown"}
                or target_seasons & {value.casefold() for value in garment.season if value.casefold() != "unknown"}
            ]
            if not {"top", "bottom", "shoes"}.issubset({garment.category for garment in garments}):
                raise HTTPException(status_code=409, detail={"code": "no_valid_combination"})

    season = body.season or current_season(settings)
    outfit_request = {
        "garments": garments,
        "occasion": body.occasion,
        "season": season,
        "temperature": body.temperature,
        "weather": body.weather,
    }
    preferences = dict(preferences)
    preferences["avoid_categories"] = sorted(avoided_categories)
    preferences["excluded_garment_ids"] = sorted(excluded_ids)
    if target_garment:
        preferences["required_garment_id"] = target_garment.id
    if preferences:
        outfit_request["preferences"] = preferences
    items, explanation = await AiService(settings).generate_outfit(**outfit_request)
    items = _trusted_outfit_items(items, garments)
    if not _valid_combination(items, garments, target_garment):
        raise HTTPException(status_code=409, detail={"code": "no_valid_combination"})
    item_labels = [
        f"{item['category']} ({', '.join(next((g.tags for g in garments if g.id == item['garment_id']), [])[:2])})"
        for item in items
    ]
    explanation = f"已从你确认的衣物中选出{'、'.join(item_labels)}，适合{body.occasion}场景。"
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
    return _outfit_payload(db, outfit, current_user.id, settings)


@router.post("/manual", response_model=OutfitResponse, status_code=201)
def create_manual_outfit(
    body: ManualOutfitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    garments = list(
        db.execute(
            select(Garment).where(
                Garment.user_id == current_user.id,
                Garment.status == "ready",
                Garment.review_status == "confirmed",
                Garment.user_confirmed_at.is_not(None),
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
            "image_url": "",
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
    return _outfit_payload(db, outfit, current_user.id, settings)


@router.get("/history", response_model=OutfitListResponse)
def list_outfits(
    favorite: bool | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> OutfitListResponse:
    query = select(Outfit).where(Outfit.user_id == current_user.id)
    if favorite is not None:
        query = query.where(Outfit.is_favorite == favorite)
    outfits = list(db.execute(query.order_by(Outfit.created_at.desc())).scalars())
    return OutfitListResponse(items=[_outfit_payload(db, outfit, current_user.id, settings) for outfit in outfits])


@router.patch("/{outfit_id}/favorite", response_model=OutfitResponse)
def update_favorite(
    outfit_id: str,
    body: FavoriteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    outfit = db.get(Outfit, outfit_id)
    if outfit is None or outfit.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Outfit not found")
    outfit.is_favorite = body.is_favorite
    db.commit()
    db.refresh(outfit)
    return _outfit_payload(db, outfit, current_user.id, settings)


@router.patch("/{outfit_id}/fixed", response_model=OutfitResponse)
def update_fixed(
    outfit_id: str,
    body: FixedUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    outfit = db.get(Outfit, outfit_id)
    if outfit is None or outfit.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Outfit not found")
    outfit.is_fixed = body.is_fixed
    db.commit()
    db.refresh(outfit)
    return _outfit_payload(db, outfit, current_user.id, settings)


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


def _outfit_payload(db: Session, outfit: Outfit, user_id: str, settings: Settings) -> dict[str, object]:
    from app.schemas import OutfitResponse

    payload = OutfitResponse.model_validate(outfit).model_dump()
    ids = list(dict.fromkeys(str(item.get("garment_id") or "") for item in outfit.items or [] if item.get("garment_id")))
    garments = {
        garment.id: garment
        for garment in db.execute(select(Garment).where(Garment.user_id == user_id, Garment.id.in_(ids))).scalars()
    } if ids else {}
    members = []
    for item in outfit.items or []:
        garment = garments.get(str(item.get("garment_id") or ""))
        members.append({
            **item,
            "category": garment.category if garment else item.get("category", ""),
            "image_url": asset_content_url(settings.jwt_secret, garment.asset_id, user_id, "thumbnail")
            if garment and garment.asset_id else "",
            "unavailable": garment is None or not bool(garment.asset_id),
        })
    payload["items"] = members
    return payload


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
                "image_url": "",
                "reason": str(item.get("reason") or "AI 推荐"),
            }
        )
    return trusted


def _target_garment(garment_id: str | None, garments: list[Garment]) -> Garment | None:
    if not garment_id:
        return None
    for garment in garments:
        if garment.id == garment_id:
            return garment
    raise HTTPException(status_code=404, detail="Target garment not found")


def _ensure_target_item(items: list[dict[str, object]], target: Garment | None) -> list[dict[str, object]]:
    if target is None or any(item.get("garment_id") == target.id for item in items):
        return items
    target_item = {
        "garment_id": target.id,
        "category": target.category,
        "image_url": target.image_url,
        "reason": "围绕指定单品生成",
    }
    return [target_item, *items]


def _valid_combination(
    items: list[dict[str, object]],
    garments: list[Garment],
    target: Garment | None,
) -> bool:
    if not items:
        return False
    by_id = {garment.id: garment for garment in garments}
    selected = [by_id.get(str(item.get("garment_id") or "")) for item in items]
    if any(garment is None for garment in selected):
        return False
    typed = [garment for garment in selected if garment is not None]
    categories = [garment.category for garment in typed]
    if len(categories) != len(set(categories)) or not {"top", "bottom", "shoes"}.issubset(set(categories)):
        return False
    if target and target.id not in {garment.id for garment in typed}:
        return False
    return True
