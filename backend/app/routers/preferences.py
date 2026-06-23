from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, UserPreference
from app.schemas import UserPreferenceResponse, UserPreferenceUpdate
from app.security import get_current_user

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.get("/me", response_model=UserPreferenceResponse)
def get_my_preferences(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserPreferenceResponse:
    preference = _get_preference(db, current_user.id)
    if preference is None:
        return UserPreferenceResponse()
    return UserPreferenceResponse.model_validate(preference)


@router.put("/me", response_model=UserPreferenceResponse)
def update_my_preferences(
    body: UserPreferenceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserPreference:
    preference = _get_preference(db, current_user.id)
    if preference is None:
        preference = UserPreference(user_id=current_user.id)
        db.add(preference)
    preference.primary_goal = body.primary_goal.strip()
    preference.scenes = _clean_list(body.scenes)
    preference.styles = _clean_list(body.styles)
    preference.avoid_types = _clean_list(body.avoid_types)
    preference.budget_range = body.budget_range.strip()
    db.commit()
    db.refresh(preference)
    return preference


def _get_preference(db: Session, user_id: str) -> UserPreference | None:
    return db.execute(select(UserPreference).where(UserPreference.user_id == user_id)).scalar_one_or_none()


def _clean_list(values: list[str]) -> list[str]:
    cleaned: list[str] = []
    for value in values:
        item = value.strip()
        if item and item not in cleaned:
            cleaned.append(item)
    return cleaned[:10]
