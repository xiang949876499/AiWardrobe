from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.ai import AiService
from app.commerce import CommerceProduct
from app.config import Settings
from app.models import Garment, PurchaseCandidate, ShoppingRecommendationItem, ShoppingRecommendationRun, User
from app.purchase_analysis import analyze_purchase, explain_purchase
from app.rate_limit import RateLimitResult, check_rate_limit
from app.storage import StorageService
from app.taobao_client import TaobaoClientError, get_taobao_client

SHOPPING_TARGETS = {"auto_gap", "work", "date", "sport", "summer", "basics"}
READY_CATEGORIES = ["top", "bottom", "outerwear", "shoes", "bag", "accessory"]
CACHE_TTL_MINUTES = 30


@dataclass(frozen=True)
class RecommendationRateLimited(Exception):
    code: str
    reset_at: datetime
    remaining_refreshes: int = 0


def generate_recommendation_keywords(target: str, garments: list[Garment]) -> list[str]:
    if target == "summer":
        return ["summer breathable top", "summer lightweight trousers", "linen skirt", "summer sandals", "canvas tote"]
    if target == "basics":
        return ["white basic top", "black versatile trousers", "neutral low heel shoes", "daily tote bag", "light cardigan"]
    if target == "work":
        return _target_keywords(garments, "work", ["tailored", "office", "commute"])
    if target == "date":
        return _target_keywords(garments, "date", ["soft", "polished", "elegant"])
    if target == "sport":
        return ["sport shoes", "quick dry top", "sport trousers", "lightweight jacket", "gym bag"]
    return _auto_gap_keywords(garments)


async def create_recommendation_run(
    db: Session,
    current_user: User,
    settings: Settings,
    target: str,
    refresh: bool,
) -> ShoppingRecommendationRun:
    cached = _recent_cached_run(db, current_user, target) if not refresh else None
    if cached is not None:
        cached.cache_hit = True
        db.commit()
        db.refresh(cached)
        return _load_run(db, cached.id)

    refresh_limit = check_rate_limit(
        db,
        scope="shopping_recommendation_refresh",
        key=current_user.id,
        limit=3,
        window_seconds=600,
    )
    if not refresh_limit.allowed:
        db.rollback()
        raise RecommendationRateLimited("recommendation_rate_limited", refresh_limit.reset_at)

    search_limit = check_rate_limit(db, scope="taobao_search", key="global", limit=30, window_seconds=60)
    if not search_limit.allowed:
        db.rollback()
        raise RecommendationRateLimited("taobao_rate_limited", search_limit.reset_at, refresh_limit.remaining)

    ready_garments = _ready_garments(db, current_user)
    keywords = generate_recommendation_keywords(target, ready_garments)
    products = _search_products(settings, keywords)
    run = ShoppingRecommendationRun(
        user_id=current_user.id,
        target=target,
        keywords=keywords,
        status="running",
        cache_hit=False,
        rate_limit=_rate_limit_payload(refresh_limit),
    )
    db.add(run)
    db.flush()

    items = [_item_from_product(run, current_user, product) for product in products[:9]]
    db.add_all(items)
    db.flush()
    for item in items[:3]:
        await analyze_recommendation_item(db, current_user, settings, item, consume_limit=False)
    run.status = "ready"
    db.commit()
    return _load_run(db, run.id)


async def analyze_recommendation_item(
    db: Session,
    current_user: User,
    settings: Settings,
    item: ShoppingRecommendationItem,
    consume_limit: bool = True,
) -> ShoppingRecommendationItem:
    if item.user_id != current_user.id:
        raise LookupError("shopping_item_not_found")
    if item.analysis_status == "analyzed" and item.purchase_candidate_id:
        return item
    reused = _reuse_existing_analysis(db, current_user, item)
    if reused is not None:
        return reused
    if consume_limit:
        limit = check_rate_limit(db, scope="shopping_item_analysis", key=current_user.id, limit=5, window_seconds=60)
        if not limit.allowed:
            db.rollback()
            raise RecommendationRateLimited("analysis_rate_limited", limit.reset_at)

    try:
        item.analysis_status = "analyzing"
        db.flush()
        image_bytes, content_type = _product_image_payload(settings, item)
        suffix = _suffix_for_image(content_type, item.image_url)
        stored = StorageService(settings).save_bytes(image_bytes, suffix=suffix, prefix="purchase")
        analysis = await AiService(settings).analyze_garment(
            filename=_analysis_filename(item),
            content_type=content_type,
            image_bytes=image_bytes,
        )
        ready_garments = _ready_garments(db, current_user)
        decision = analyze_purchase(analysis, ready_garments)
        reason_summary = await explain_purchase(settings, decision, analysis)
        candidate = PurchaseCandidate(
            user_id=current_user.id,
            product_url=item.product_url,
            source_image_url=item.image_url,
            image_url=stored.url,
            image_key=stored.key,
            thumbnail_url=stored.url,
            title=item.title,
            domain=urlparse(item.product_url).netloc.lower() or item.platform,
            category=analysis.category,
            colors=analysis.colors,
            style=analysis.style,
            material=analysis.material,
            season=analysis.season,
            fit=analysis.fit,
            tags=analysis.tags,
            ai_result=analysis.raw,
            ai_confidence=analysis.confidence,
            similar_items=[similar.__dict__ for similar in decision.similar_items],
            recommendation=decision.recommendation,
            score=decision.score,
            reason_summary=reason_summary,
            analysis=decision.analysis,
            status="ready",
        )
        db.add(candidate)
        db.flush()
        item.purchase_candidate_id = candidate.id
        item.recommendation = decision.recommendation
        item.score = decision.score
        item.reason_summary = reason_summary
        item.analysis_status = "analyzed"
    except Exception:
        item.analysis_status = "failed"
        item.purchase_candidate_id = None
        item.recommendation = None
        item.score = None
        item.reason_summary = ""
    db.commit()
    db.refresh(item)
    return item


def get_owned_recommendation_item(db: Session, current_user: User, item_id: str) -> ShoppingRecommendationItem:
    item = db.get(ShoppingRecommendationItem, item_id)
    if item is None or item.user_id != current_user.id:
        raise LookupError("shopping_item_not_found")
    return item


def _auto_gap_keywords(garments: list[Garment]) -> list[str]:
    ready = [garment for garment in garments if garment.status == "ready"]
    counts = {category: 0 for category in READY_CATEGORIES}
    colors: set[str] = set()
    seasons: set[str] = set()
    for garment in ready:
        if garment.category in counts:
            counts[garment.category] += 1
        colors.update(color.lower() for color in garment.colors)
        seasons.update(season.lower() for season in garment.season)

    missing = sorted(READY_CATEGORIES, key=lambda category: (counts[category], READY_CATEGORIES.index(category)))
    keywords: list[str] = []
    for category in missing:
        if category == "top":
            keywords.append("versatile basic top")
        elif category == "bottom":
            keywords.append("work skirt")
        elif category == "outerwear":
            keywords.append("light cardigan")
        elif category == "shoes":
            keywords.append("black low heel shoes" if "black" not in colors else "neutral everyday shoes")
        elif category == "bag":
            keywords.append("daily commute bag")
        elif category == "accessory":
            keywords.append("simple belt accessory")
        if len(keywords) == 5:
            break
    if "summer" not in seasons and len(keywords) < 5:
        keywords.append("summer lightweight trousers")
    return keywords[:5]


def _target_keywords(garments: list[Garment], target: str, modifiers: list[str]) -> list[str]:
    base = _auto_gap_keywords(garments)
    result: list[str] = []
    for index, keyword in enumerate(base or ["top", "bottom", "shoes"]):
        result.append(f"{modifiers[index % len(modifiers)]} {target} {keyword}")
    return result[:5]


def _recent_cached_run(db: Session, current_user: User, target: str) -> ShoppingRecommendationRun | None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=CACHE_TTL_MINUTES)
    return db.execute(
        select(ShoppingRecommendationRun)
        .options(selectinload(ShoppingRecommendationRun.items))
        .where(
            ShoppingRecommendationRun.user_id == current_user.id,
            ShoppingRecommendationRun.target == target,
            ShoppingRecommendationRun.status == "ready",
            ShoppingRecommendationRun.created_at >= cutoff,
        )
        .order_by(ShoppingRecommendationRun.created_at.desc())
    ).scalars().first()


def _ready_garments(db: Session, current_user: User) -> list[Garment]:
    return list(
        db.execute(
            select(Garment).where(Garment.user_id == current_user.id, Garment.status == "ready")
        ).scalars()
    )


def _search_products(settings: Settings, keywords: list[str]) -> list[CommerceProduct]:
    try:
        return get_taobao_client(settings).search(keywords, limit=9)
    except TaobaoClientError:
        if settings.shopping_recommendation_demo_mode:
            return []
        raise


def _reuse_existing_analysis(
    db: Session,
    current_user: User,
    item: ShoppingRecommendationItem,
) -> ShoppingRecommendationItem | None:
    existing = db.execute(
        select(ShoppingRecommendationItem).where(
            ShoppingRecommendationItem.user_id == current_user.id,
            ShoppingRecommendationItem.platform == item.platform,
            ShoppingRecommendationItem.platform_item_id == item.platform_item_id,
            ShoppingRecommendationItem.id != item.id,
            ShoppingRecommendationItem.analysis_status == "analyzed",
            ShoppingRecommendationItem.purchase_candidate_id.is_not(None),
        )
    ).scalars().first()
    if existing is None:
        return None
    item.purchase_candidate_id = existing.purchase_candidate_id
    item.recommendation = existing.recommendation
    item.score = existing.score
    item.reason_summary = existing.reason_summary
    item.analysis_status = "analyzed"
    db.commit()
    db.refresh(item)
    return item


def _item_from_product(
    run: ShoppingRecommendationRun,
    current_user: User,
    product: CommerceProduct,
) -> ShoppingRecommendationItem:
    return ShoppingRecommendationItem(
        run_id=run.id,
        user_id=current_user.id,
        platform=product.platform,
        platform_item_id=product.platform_item_id,
        title=product.title,
        image_url=product.image_url,
        price=product.price,
        shop_name=product.shop_name,
        product_url=product.product_url,
        raw=product.raw,
        analysis_status="pending_analysis",
    )


def _load_run(db: Session, run_id: str) -> ShoppingRecommendationRun:
    run = db.execute(
        select(ShoppingRecommendationRun)
        .options(selectinload(ShoppingRecommendationRun.items))
        .where(ShoppingRecommendationRun.id == run_id)
    ).scalar_one()
    run.items.sort(key=lambda item: item.created_at)
    return run


def _rate_limit_payload(limit: RateLimitResult) -> dict[str, object]:
    return {"remaining_refreshes": limit.remaining, "reset_at": limit.reset_at.isoformat()}


def _product_image_payload(settings: Settings, item: ShoppingRecommendationItem) -> tuple[bytes, str]:
    if settings.shopping_recommendation_demo_mode:
        return f"demo image for {item.title}".encode("utf-8"), "image/jpeg"
    raise TaobaoClientError("image_download_failed")


def _analysis_filename(item: ShoppingRecommendationItem) -> str:
    words = re.sub(r"[^a-zA-Z0-9]+", "-", item.title.lower()).strip("-")
    return f"{words or item.platform_item_id}.jpg"


def _suffix_for_image(content_type: str, source_image_url: str) -> str:
    suffix = Path(urlparse(source_image_url).path).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return suffix
    return {
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/jpeg": ".jpg",
    }.get(content_type.lower(), ".jpg")
