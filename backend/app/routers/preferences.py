from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.decision_context import bump_revision
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
    preference = db.execute(select(UserPreference).where(UserPreference.user_id == current_user.id).with_for_update()).scalar_one_or_none()
    if preference is None:
        preference = UserPreference(user_id=current_user.id)
        db.add(preference)
        db.flush()
    updates = {
        "primary_goal": body.primary_goal.strip(),
        "scenes": _clean_list(body.scenes),
        "styles": _clean_list(body.styles),
        "avoid_types": _clean_list(body.avoid_types),
        "budget_range": body.budget_range.strip(),
    }
    for field in ("budget_min", "budget_max", "currency", "avoid_categories"):
        if field in body.model_fields_set:
            value = getattr(body, field)
            updates[field] = _clean_list(value) if field == "avoid_categories" else value
    changed = any(getattr(preference, key) != value for key, value in updates.items())
    if changed:
        for key, value in updates.items():
            setattr(preference, key, value)
        bump_revision(db, current_user.id, "preference_revision")
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
