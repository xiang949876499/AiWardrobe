from __future__ import annotations

import base64
import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.orm import Session

from app.asset_access import create_asset_token
from app.config import Settings, get_settings
from app.database import get_db
from app.models import Garment, Outfit, User
from app.security import get_current_user

router = APIRouter(prefix="/v2", tags=["paginated-wardrobe"])


@router.get("/garments")
def list_garments_v2(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    category: str | None = None,
    tag: str | None = None,
    color: str | None = None,
    season: str | None = None,
    q: str | None = Query(default=None, max_length=120),
    status: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    filters = {"category": category, "tag": tag, "color": color, "season": season, "q": q, "status": status}
    query = select(Garment).where(Garment.user_id == current_user.id)
    if category:
        query = query.where(Garment.category == category)
    if status:
        query = query.where(Garment.status == status)
    if tag:
        query = query.where(_json_array_has(Garment.tags, tag, db))
    if color:
        query = query.where(_json_array_has(Garment.colors, color, db))
    if season:
        query = query.where(_json_array_has(Garment.season, season, db))
    if q:
        pattern = f"%{q.strip()}%"
        query = query.where(or_(
            Garment.category.ilike(pattern),
            Garment.style.ilike(pattern),
            Garment.material.ilike(pattern),
            Garment.fit.ilike(pattern),
            cast(Garment.tags, String).ilike(pattern),
            cast(Garment.colors, String).ilike(pattern),
        ))
    if cursor:
        created_at, garment_id = _decode_cursor(cursor, current_user.id, filters)
        query = query.where(or_(Garment.created_at < created_at, and_(Garment.created_at == created_at, Garment.id < garment_id)))
    rows = list(db.execute(query.order_by(Garment.created_at.desc(), Garment.id.desc()).limit(limit + 1)).scalars())
    has_more = len(rows) > limit
    items = rows[:limit]
    return {
        "items": [_garment_payload(garment, current_user.id, settings) for garment in items],
        "next_cursor": _encode_cursor(items[-1], current_user.id, filters) if has_more and items else None,
        "has_more": has_more,
    }


@router.get("/garments/{garment_id}")
def get_garment_v2(
    garment_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    garment = db.execute(select(Garment).where(Garment.id == garment_id, Garment.user_id == current_user.id)).scalar_one_or_none()
    if garment is None:
        raise HTTPException(status_code=404, detail={"code": "garment_not_found"})
    return _garment_payload(garment, current_user.id, settings)


@router.get("/outfits")
def list_outfits_v2(
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    favorite: bool | None = None,
    fixed: bool | None = None,
    occasion: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    filters = {"favorite": favorite, "fixed": fixed, "occasion": occasion}
    query = select(Outfit).where(Outfit.user_id == current_user.id)
    if favorite is not None:
        query = query.where(Outfit.is_favorite == favorite)
    if fixed is not None:
        query = query.where(Outfit.is_fixed == fixed)
    if occasion:
        query = query.where(Outfit.occasion == occasion)
    if cursor:
        created_at, outfit_id = _decode_cursor(cursor, current_user.id, filters)
        query = query.where(or_(Outfit.created_at < created_at, and_(Outfit.created_at == created_at, Outfit.id < outfit_id)))
    rows = list(db.execute(query.order_by(Outfit.created_at.desc(), Outfit.id.desc()).limit(limit + 1)).scalars())
    has_more = len(rows) > limit
    items = rows[:limit]
    return {
        "items": [_outfit_payload(db, outfit, current_user.id, settings) for outfit in items],
        "next_cursor": _encode_cursor(items[-1], current_user.id, filters) if has_more and items else None,
        "has_more": has_more,
    }


@router.get("/outfits/{outfit_id}")
def get_outfit_v2(
    outfit_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    outfit = db.execute(select(Outfit).where(Outfit.id == outfit_id, Outfit.user_id == current_user.id)).scalar_one_or_none()
    if outfit is None:
        raise HTTPException(status_code=404, detail={"code": "outfit_not_found"})
    return _outfit_payload(db, outfit, current_user.id, settings)


def _garment_payload(garment: Garment, user_id: str, settings: Settings) -> dict[str, object]:
    image_url = None
    if garment.asset_id:
        token, _ = create_asset_token(settings.jwt_secret, garment.asset_id, user_id, "thumbnail")
        image_url = f"/v2/assets/{garment.asset_id}/content?variant=thumbnail&asset_token={token}"
    return {
        "id": garment.id,
        "source_upload_id": garment.source_upload_id,
        "asset_id": garment.asset_id,
        "image_url": image_url or "",
        "image_key": "",
        "thumbnail_url": image_url or None,
        "category": garment.category,
        "colors": garment.colors or [],
        "style": garment.style,
        "material": garment.material,
        "season": garment.season or [],
        "fit": garment.fit,
        "tags": garment.tags or [],
        "status": garment.status,
        "review_status": garment.review_status,
        "crop_box": garment.crop_box,
        "ai_result": garment.ai_result or {},
        "ai_confidence": garment.ai_confidence,
        "analysis_source": garment.analysis_source,
        "provider": garment.provider,
        "model_version": garment.model_version,
        "analyzed_at": garment.analyzed_at,
        "user_confirmed_at": garment.user_confirmed_at,
        "created_at": garment.created_at,
        "updated_at": garment.updated_at,
    }


def _outfit_payload(db: Session, outfit: Outfit, user_id: str, settings: Settings) -> dict[str, object]:
    ids = list(dict.fromkeys(str(item.get("garment_id")) for item in outfit.items or [] if item.get("garment_id")))
    garments = {item.id: item for item in db.execute(select(Garment).where(Garment.user_id == user_id, Garment.id.in_(ids))).scalars()} if ids else {}
    members = []
    for old in outfit.items or []:
        garment_id = str(old.get("garment_id") or "")
        garment = garments.get(garment_id)
        asset_url = None
        if garment and garment.asset_id:
            token, _ = create_asset_token(settings.jwt_secret, garment.asset_id, user_id, "thumbnail")
            asset_url = f"/v2/assets/{garment.asset_id}/content?variant=thumbnail&asset_token={token}"
        members.append({
            "garment_id": garment_id,
            "category": garment.category if garment else old.get("category"),
            "image_url": asset_url,
            "unavailable": garment is None or not bool(garment.asset_id),
            "reason": old.get("reason", ""),
        })
    return {
        "id": outfit.id,
        "name": outfit.name,
        "occasion": outfit.occasion,
        "season": outfit.season,
        "temperature": outfit.temperature,
        "items": members,
        "explanation": outfit.explanation,
        "source": outfit.source,
        "is_favorite": outfit.is_favorite,
        "is_fixed": outfit.is_fixed,
        "result_revision": outfit.result_revision,
        "weather_snapshot": outfit.weather_snapshot,
        "created_at": outfit.created_at,
    }


def _encode_cursor(row, user_id: str, filters: dict[str, object]) -> str:
    payload = {"user": user_id, "filters": filters, "created_at": row.created_at.isoformat(), "id": row.id}
    return base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode_cursor(cursor: str, user_id: str, filters: dict[str, object]) -> tuple[datetime, str]:
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if payload.get("user") != user_id or payload.get("filters") != filters:
            raise ValueError
        return datetime.fromisoformat(payload["created_at"]), str(UUID(payload["id"]))
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_cursor"}) from exc


def _json_array_has(column, value: str, db: Session):
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        values = func.json_each(column).table_valued("value").alias()
        return select(1).select_from(values).where(values.c.value == value).correlate(Garment).exists()
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import JSONB

        return cast(column, JSONB).contains([value])
    return column.contains([value])
