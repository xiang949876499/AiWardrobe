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
        "budget_min": preference.budget_min,
        "budget_max": preference.budget_max,
        "currency": preference.currency,
        "avoid_categories": preference.avoid_categories or [],
    }
    if not any(
        value
        for key, value in context.items()
        if key not in {"budget_min", "budget_max", "currency", "avoid_categories"}
    ) and context["budget_min"] is None and context["budget_max"] is None and not context["avoid_categories"]:
        return None
    return context
