"""Selected, scoped portrait references shared by frame and video workers."""
from fastapi import HTTPException
from sqlalchemy import select
from app.config import get_settings
from app.models import WeddingAsset, WeddingAssetType
from app.wedding_service import validate_active_consents, _as_utc
from app.models import utc_now


def selected_references(db, project, person_keys, *, demo=False):
    if len(set(person_keys)) != len(person_keys) or any(key not in ('A', 'B') for key in person_keys):
        raise HTTPException(409, 'PERSON_SCOPE_INVALID')
    people = {p.person_key: p for p in validate_active_consents(db, project)}
    result = []
    for key in sorted(person_keys):
        asset_id = people[key].primary_photo_asset_id
        asset = db.get(WeddingAsset, asset_id) if asset_id else None
        if asset is None and demo:
            asset = db.scalar(select(WeddingAsset).where(WeddingAsset.project_id == project.id,
                WeddingAsset.person_key == key, WeddingAsset.asset_type == WeddingAssetType.PHOTO,
                WeddingAsset.generated.is_(False)).order_by(WeddingAsset.created_at, WeddingAsset.id))
        if asset is None:
            if demo:
                continue
            raise HTTPException(409, 'PRIMARY_PHOTO_REQUIRED')
        if asset.project_id != project.id or asset.person_key != key or asset.generated or asset.asset_type != WeddingAssetType.PHOTO or (asset.expires_at and _as_utc(asset.expires_at) <= utc_now()):
            raise HTTPException(409, 'PRIMARY_PHOTO_INVALID')
        result.append({'person_key': key, 'asset_id': asset.id, 'object_key': asset.object_key, 'sha256': asset.sha256, 'mime': asset.mime})
    return result


def consent_provenance(db, project, provider):
    people = validate_active_consents(db, project)
    for person in people:
        if provider not in person.provider_list:
            raise HTTPException(409, 'PROVIDER_CONSENT_REQUIRED')
    return [{'participant_id': p.id, 'person_key': p.person_key,
             'confirmed_at': p.confirmed_at.isoformat(), 'terms_version': p.terms_version,
             'provider': provider} for p in people]
