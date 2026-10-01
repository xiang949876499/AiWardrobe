from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.asset_access import InvalidAssetToken, create_asset_token, verify_asset_token
from app.asset_storage import PrivateAssetStorage
from app.config import Settings, get_settings
from app.database import get_db
from app.models import AnalysisTask, Garment, PurchaseCandidate, StoredAsset, UploadSession, User
from app.security import get_current_user

router = APIRouter(prefix="/v2/assets", tags=["assets"])


@router.get("/{asset_id}/access")
def create_asset_access_url(
    asset_id: str,
    variant: Literal["original", "thumbnail"] = Query(default="original"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> dict[str, object]:
    asset = _owned_referenced_asset(db, current_user.id, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail={"code": "asset_not_found"})
    if variant == "thumbnail":
        thumbnail = db.get(StoredAsset, asset.thumbnail_asset_id) if asset.thumbnail_asset_id else None
        if thumbnail is None or thumbnail.user_id != current_user.id:
            raise HTTPException(status_code=404, detail={"code": "asset_not_found"})
    token, expires_at = create_asset_token(settings.jwt_secret, asset.id, current_user.id, variant)
    return {
        "url": f"/v2/assets/{asset.id}/content?variant={variant}&asset_token={token}",
        "expires_at": datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat(),
    }


@router.get("/{asset_id}/content")
def read_asset_content(
    asset_id: str,
    variant: Literal["original", "thumbnail"],
    asset_token: str = Query(min_length=20, max_length=512),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        claims = verify_asset_token(
            settings.jwt_secret,
            asset_token,
            asset_id=asset_id,
            variant=variant,
        )
    except InvalidAssetToken as exc:
        raise HTTPException(status_code=404, detail={"code": "asset_not_found"}) from exc
    asset = _owned_referenced_asset(db, claims.user_id, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail={"code": "asset_not_found"})
    selected = asset
    if variant == "thumbnail":
        selected = db.get(StoredAsset, asset.thumbnail_asset_id) if asset.thumbnail_asset_id else None
        if selected is None or selected.user_id != claims.user_id:
            raise HTTPException(status_code=404, detail={"code": "asset_not_found"})
    data = PrivateAssetStorage(settings).read(selected.storage_key)
    return Response(
        content=data,
        media_type=selected.mime_type,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


def _owned_referenced_asset(db: Session, user_id: str, asset_id: str) -> StoredAsset | None:
    asset = db.get(StoredAsset, asset_id)
    if asset is None or asset.user_id != user_id:
        return None
    garment_exists = db.execute(
        select(Garment.id).where(Garment.user_id == user_id, Garment.asset_id == asset_id).limit(1)
    ).first()
    if garment_exists:
        return asset
    candidate_exists = db.execute(
        select(PurchaseCandidate.id).where(
            PurchaseCandidate.user_id == user_id,
            PurchaseCandidate.asset_id == asset_id,
        ).limit(1)
    ).first()
    if candidate_exists:
        return asset
    upload_exists = db.execute(
        select(UploadSession.id).where(UploadSession.user_id == user_id, UploadSession.asset_id == asset_id).limit(1)
    ).first()
    if upload_exists:
        return asset
    tasks = db.execute(
        select(AnalysisTask.input_refs).where(
            AnalysisTask.user_id == user_id,
            AnalysisTask.status.in_(["queued", "running"]),
        )
    ).scalars()
    if any((references or {}).get("asset_id") == asset_id for references in tasks):
        return asset
    return None
