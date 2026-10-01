from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.asset_storage import PrivateAssetStorage
from app.config import Settings
from app.image_inputs import PreparedImage, prepare_image
from app.models import StoredAsset, new_id


@dataclass(frozen=True)
class StoredImageAsset:
    original: StoredAsset
    prepared: PreparedImage


def create_image_asset(
    db: Session,
    settings: Settings,
    user_id: str,
    data: bytes,
    claimed_content_type: str | None,
) -> StoredImageAsset:
    prepared = prepare_image(data, claimed_content_type)
    return store_prepared_image(db, settings, user_id, prepared)


def store_prepared_image(
    db: Session,
    settings: Settings,
    user_id: str,
    prepared: PreparedImage,
) -> StoredImageAsset:
    storage = PrivateAssetStorage(settings)
    thumbnail_type = "image/png" if prepared.content_type == "image/png" else prepared.content_type
    original_key = storage.write(user_id, prepared.content_type, prepared.data)
    try:
        thumbnail_key = storage.write(user_id, thumbnail_type, prepared.thumbnail)
    except Exception:
        storage.delete(original_key)
        raise
    thumbnail = StoredAsset(
        id=new_id(),
        user_id=user_id,
        storage_key=thumbnail_key,
        mime_type=thumbnail_type,
        byte_size=len(prepared.thumbnail),
        width=min(prepared.width, 480),
        height=min(prepared.height, 480),
        sha256=hashlib.sha256(prepared.thumbnail).hexdigest(),
    )
    original = StoredAsset(
        id=new_id(),
        user_id=user_id,
        storage_key=original_key,
        mime_type=prepared.content_type,
        byte_size=len(prepared.data),
        width=prepared.width,
        height=prepared.height,
        sha256=prepared.sha256,
        thumbnail_asset_id=thumbnail.id,
    )
    db.add_all([original, thumbnail])
    db.flush()
    return StoredImageAsset(original=original, prepared=prepared)


def garment_response(garment, settings: Settings) -> dict[str, object]:
    from app.asset_access import asset_content_url
    from app.schemas import GarmentResponse

    payload = GarmentResponse.model_validate(garment).model_dump()
    image_url = asset_content_url(settings.jwt_secret, garment.asset_id, garment.user_id, "thumbnail") if garment.asset_id else ""
    payload.update({"image_url": image_url, "thumbnail_url": image_url, "image_key": ""})
    return payload


def upload_session_response(upload, settings: Settings) -> dict[str, object]:
    from app.asset_access import asset_content_url
    from app.schemas import UploadSessionResponse

    payload = UploadSessionResponse.model_validate(upload).model_dump()
    original_url = asset_content_url(settings.jwt_secret, upload.asset_id, upload.user_id, "original") if upload.asset_id else ""
    payload.update({
        "original_image_url": original_url,
        "original_image_key": "",
        "garments": [garment_response(garment, settings) for garment in upload.garments],
    })
    return payload


def candidate_response(db: Session, candidate, settings: Settings) -> dict[str, object]:
    from app.asset_access import asset_content_url
    from app.schemas import PurchaseCandidateResponse
    from app.models import Garment

    payload = PurchaseCandidateResponse.model_validate(candidate).model_dump()
    image_url = asset_content_url(settings.jwt_secret, candidate.asset_id, candidate.user_id, "thumbnail") if candidate.asset_id else ""
    payload.update({"image_url": image_url, "thumbnail_url": image_url, "image_key": ""})
    similar_items = []
    ids = [str(item.get("garment_id") or "") for item in candidate.similar_items or []]
    garments = {
        garment.id: garment
        for garment in db.execute(select(Garment).where(Garment.user_id == candidate.user_id, Garment.id.in_(ids))).scalars()
    } if ids else {}
    for item in candidate.similar_items or []:
        garment = garments.get(str(item.get("garment_id") or ""))
        similar_items.append({
            **item,
            "image_url": asset_content_url(settings.jwt_secret, garment.asset_id, candidate.user_id, "thumbnail")
            if garment and garment.asset_id else "",
        })
    payload["similar_items"] = similar_items
    if isinstance(payload.get("analysis"), dict):
        payload["analysis"] = {**payload["analysis"], "similar_items": similar_items,
                               "outfit_ideas": purchase_outfit_ideas_response(db, candidate, payload["analysis"], settings)}
    return payload


def purchase_outfit_ideas_response(db: Session, candidate, analysis: dict[str, object], settings: Settings) -> list[dict[str, object]]:
    """Resolve current owned references without modifying historical decisions."""
    from app.asset_access import asset_content_url
    from app.feedback_service import active_excluded_garments
    from app.models import Garment
    from app.preferences import preference_context
    from app.purchase_analysis import _CORE_CATEGORIES, _OUTFIT_CATEGORIES, _season_evidence

    groups = analysis.get("outfit_ideas") or []
    groups = [group for group in groups if isinstance(group, dict) and isinstance(group.get("items"), list)]
    if not groups:
        return []
    ids = {str(item.get("garment_id")) for group in groups for item in group["items"]
           if isinstance(item, dict) and item.get("garment_id")}
    garments = {garment.id: garment for garment in db.execute(select(Garment).where(
        Garment.user_id == candidate.user_id, Garment.id.in_(ids), Garment.status == "ready",
        Garment.review_status == "confirmed", Garment.user_confirmed_at.is_not(None),
    )).scalars()} if ids else {}
    excluded_ids = active_excluded_garments(db, candidate.user_id)
    avoided = set((preference_context(db, candidate.user_id) or {}).get("avoid_categories") or [])
    asset_ids = {garment.asset_id for garment in garments.values() if garment.asset_id}
    if candidate.asset_id:
        asset_ids.add(candidate.asset_id)
    assets = {asset.id for asset in db.execute(select(StoredAsset).where(
        StoredAsset.user_id == candidate.user_id, StoredAsset.id.in_(asset_ids),
    )).scalars()} if asset_ids else set()
    result = []
    for group in groups:
        resolved, originals, categories, owned_ids = [], [candidate], set(), set()
        candidate_count = 0
        valid = True
        for item in group["items"]:
            if not isinstance(item, dict):
                valid = False
                break
            if item.get("candidate_id") and not item.get("garment_id"):
                resource = candidate if item["candidate_id"] == candidate.id else None
                candidate_count += 1
            elif item.get("garment_id") and not item.get("candidate_id"):
                resource = garments.get(str(item["garment_id"]))
                if resource and (resource.id in excluded_ids or resource.id in owned_ids):
                    resource = None
                if resource:
                    originals.append(resource)
                    owned_ids.add(resource.id)
            else:
                resource = None
            if (resource is None or resource.category != item.get("category")
                    or resource.category not in _OUTFIT_CATEGORIES or resource.category in categories
                    or resource.category in avoided):
                valid = False
                break
            categories.add(resource.category)
            ref = {key: value for key, value in item.items() if key not in {"image_url", "asset_id"}}
            if resource.asset_id in assets:
                ref["asset_id"] = resource.asset_id
                ref["image_url"] = asset_content_url(settings.jwt_secret, resource.asset_id, candidate.user_id, "thumbnail")
            resolved.append(ref)
        if not valid or candidate_count != 1 or not set(_CORE_CATEGORIES) <= categories:
            continue
        seasons_valid, seasons, warnings = _season_evidence(originals)
        if seasons_valid:
            result.append({**group, "items": resolved, "seasons": seasons, "warnings": warnings})
        if len(result) == 3:
            break
    return result
