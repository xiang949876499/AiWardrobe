from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.decision_context import get_decision_context
from app.models import Garment, User, WearEvent, WearEventItem
from app.schemas import WardrobeReportResponse
from app.security import get_current_user
from app.wardrobe_report import build_wardrobe_report

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/wardrobe", response_model=WardrobeReportResponse)
def wardrobe_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    garments = list(db.execute(select(Garment).where(Garment.user_id == current_user.id)).scalars())
    report = build_wardrobe_report(garments)
    context = get_decision_context(db, current_user.id)
    now = datetime.now(timezone.utc)
    report["wear_tracking_started_at"] = context.wear_tracking_started_at
    valid_events: list[WearEvent] = []
    tracking_date = None
    if context.wear_tracking_started_at and context.wear_tracking_timezone:
        tracking_date = context.wear_tracking_started_at.astimezone(ZoneInfo(context.wear_tracking_timezone)).date().isoformat()
        valid_events = list(db.execute(select(WearEvent).where(
            WearEvent.user_id == current_user.id,
            WearEvent.revoked_at.is_(None),
            WearEvent.local_date >= tracking_date,
        )).scalars())
    observed_days = len({event.local_date for event in valid_events})
    report["wear_days"] = observed_days
    if not valid_events:
        report["wear_data_status"] = "none"
    elif context.wear_tracking_started_at and context.wear_tracking_started_at + timedelta(days=30) <= now and observed_days >= 5:
        report["wear_data_status"] = "available"
        cutoff_date = (now.astimezone(ZoneInfo(context.wear_tracking_timezone or "UTC")).date() - timedelta(days=30)).isoformat()
        recent_rows = db.execute(
            select(WearEventItem.garment_id, WearEvent.local_date)
            .join(WearEvent, WearEvent.id == WearEventItem.wear_event_id)
            .where(
                WearEvent.user_id == current_user.id,
                WearEvent.revoked_at.is_(None),
                WearEvent.local_date >= cutoff_date,
                WearEvent.local_date >= tracking_date,
            )
        ).all()
        recently_worn = {garment_id for garment_id, _ in recent_rows}
        report["low_use_items"] = [
            {
                "garment_id": garment.id,
                "category": garment.category,
                "label": ", ".join((garment.colors or [])[:2]) or garment.category,
                "reason": "近期未记录穿着",
                "score": 0,
            }
            for garment in garments
            if garment.status == "ready"
            and garment.review_status == "confirmed"
            and garment.user_confirmed_at is not None
            and garment.created_at <= now - timedelta(days=30)
            and garment.id not in recently_worn
        ]
    else:
        report["wear_data_status"] = "insufficient"
    return report
