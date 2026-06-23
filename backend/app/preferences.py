from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import UserPreference


def preference_context(db: Session, user_id: str) -> dict[str, object] | None:
    preference = db.execute(select(UserPreference).where(UserPreference.user_id == user_id)).scalar_one_or_none()
    if preference is None:
        return None
    context = {
        "primary_goal": preference.primary_goal,
        "scenes": preference.scenes,
        "styles": preference.styles,
        "avoid_types": preference.avoid_types,
        "budget_range": preference.budget_range,
    }
    if not any(context.values()):
        return None
    return context
