from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Garment, User
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
    return build_wardrobe_report(garments)
