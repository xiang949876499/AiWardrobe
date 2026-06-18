from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.models import ShoppingRecommendationItem, ShoppingRecommendationRun, User
from app.schemas import (
    ShoppingRecommendationItemResponse,
    ShoppingRecommendationRequest,
    ShoppingRecommendationRunResponse,
)
from app.security import get_current_user
from app.shopping_recommendations import (
    RecommendationRateLimited,
    analyze_recommendation_item,
    create_recommendation_run,
    get_owned_recommendation_item,
)

router = APIRouter(prefix="/shopping", tags=["shopping"])


@router.post(
    "/recommendations",
    response_model=ShoppingRecommendationRunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def recommend_shopping_items(
    body: ShoppingRecommendationRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ShoppingRecommendationRun:
    try:
        run = await create_recommendation_run(
            db=db,
            current_user=current_user,
            settings=settings,
            target=body.target,
            refresh=body.refresh,
        )
    except RecommendationRateLimited as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": exc.code, "reset_at": exc.reset_at.isoformat()},
        ) from exc
    if run.cache_hit:
        response.status_code = status.HTTP_200_OK
    return run


@router.post(
    "/recommendations/items/{item_id}/analyze",
    response_model=ShoppingRecommendationItemResponse,
)
async def analyze_shopping_recommendation_item(
    item_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
    settings: Settings = Depends(get_settings),
) -> ShoppingRecommendationItem:
    try:
        item = get_owned_recommendation_item(db, current_user, item_id)
        return await analyze_recommendation_item(db, current_user, settings, item)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Shopping recommendation item not found") from exc
    except RecommendationRateLimited as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": exc.code, "reset_at": exc.reset_at.isoformat()},
        ) from exc
