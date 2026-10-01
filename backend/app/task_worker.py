from __future__ import annotations

import anyio
import asyncio
import hashlib
import json
import logging
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.ai import AiAnalysis, AiAnalysisError, AiService
from app.asset_service import create_image_asset
from app.asset_storage import PrivateAssetStorage
from app.config import Settings, get_settings
from app.comfyui import ComfyUIClient
from app.database import get_session_factory, init_db
from app.decision_context import create_purchase_snapshot
from app.feedback_service import active_excluded_garments, matching_feedback_adjustment
from app.image_inputs import ImageInputError
from app.models import (
    AnalysisTask,
    Garment,
    ProviderOperation,
    PurchaseCandidate,
    ShoppingRecommendationItem,
    StoredAsset,
    User,
    new_id,
    utc_now,
)
from app.provider_recovery import LostTaskLease, ProviderPending, ProviderTerminalFailure, queryable, recovery_time
from app.runninghub import RunningHubClient, RunningHubWorkflow
from app.preferences import preference_context
from app.provider_capacity import release_provider_slot, reserve_provider_slot
from app.product_extraction import ProductExtractionError, download_public_image, extract_product_image
from app.purchase_analysis import analyze_purchase
from app.rate_limit import check_rate_limit
from app.task_service import advance_task_stage

logger = logging.getLogger(__name__)
LEASE_SECONDS = 60
HEARTBEAT_SECONDS = 10


class TaskWorker:
    def __init__(self, sessions: sessionmaker[Session], settings: Settings) -> None:
        self.sessions = sessions
        self.settings = settings

    async def run_once(self) -> bool:
        claimed = self._claim_next()
        if claimed is None:
            return False
        task_id, lease_token = claimed
        async with anyio.create_task_group() as group:
            group.start_soon(self._heartbeat_loop, task_id, lease_token)
            try:
                await self._process(task_id, lease_token)
                self._schedule_recovery(task_id)
            except LostTaskLease:
                pass
            finally:
                group.cancel_scope.cancel()
        return True

    def _claim_next(self) -> tuple[str, str] | None:
        now = utc_now()
        with self.sessions() as db:
            # Recover a crash between releasing the lease and scheduling the next read.
            stranded = list(db.execute(select(AnalysisTask).where(
                AnalysisTask.status.in_(["running", "cancelled"]), AnalysisTask.stage == "awaiting_resolution",
                AnalysisTask.lease_token.is_(None), AnalysisTask.next_attempt_at.is_(None),
                or_(AnalysisTask.error_code.is_(None), AnalysisTask.error_code != "provider_recovery_timeout"),
                select(ProviderOperation.id).where(
                    ProviderOperation.task_id == AnalysisTask.id,
                    ProviderOperation.provider_task_id.is_not(None),
                    ProviderOperation.provider.in_(["runninghub", "comfyui"]),
                    ProviderOperation.submission_status.in_(["submitted", "unknown", "submitting"]),
                ).exists(),
            ).limit(100).with_for_update(skip_locked=True)).scalars())
            for stranded_task in stranded:
                stranded_task.next_attempt_at = now
            if stranded:
                db.commit()
            expired = list(
                db.execute(
                    select(AnalysisTask)
                    .where(
                        AnalysisTask.status.in_(["running", "cancelled"]),
                        AnalysisTask.lease_expires_at.is_not(None),
                        AnalysisTask.lease_expires_at <= now,
                    )
                    .with_for_update(skip_locked=True)
                ).scalars()
            )
            for expired_task in expired:
                operations = list(
                    db.execute(
                        select(ProviderOperation).where(ProviderOperation.task_id == expired_task.id)
                    ).scalars()
                )
                has_ambiguous_submit = any(
                    operation.submission_status in {"unknown", "submitting", "submitted"}
                    for operation in operations
                )
                expired_task.lease_token = None
                expired_task.lease_expires_at = None
                if has_ambiguous_submit:
                    for operation in operations:
                        if operation.submission_status == "submitting":
                            operation.submission_status = "unknown"
                    advance_task_stage(expired_task, "awaiting_resolution")
                    expired_task.next_attempt_at = now if any(queryable(op) for op in operations) else None
                elif expired_task.cancel_requested:
                    self._finish_cancelled(expired_task)
                else:
                    expired_task.status = "queued"
                    expired_task.next_attempt_at = now
                expired_task.updated_at = now
            if expired:
                db.commit()

            query = (
                select(AnalysisTask)
                .where(
                    or_(and_(AnalysisTask.status == "queued", AnalysisTask.cancel_requested.is_(False)),
                        and_(AnalysisTask.status.in_(["running", "cancelled"]), AnalysisTask.stage == "awaiting_resolution",
                             AnalysisTask.lease_token.is_(None), AnalysisTask.next_attempt_at.is_not(None))),
                    or_(AnalysisTask.next_attempt_at.is_(None), AnalysisTask.next_attempt_at <= now),
                )
                .order_by(AnalysisTask.created_at, AnalysisTask.id)
                .limit(100)
                .with_for_update(skip_locked=True)
            )
            tasks = list(db.execute(query).scalars())
            for task in tasks:
                user_exists = db.execute(
                    select(User.id).where(User.id == task.user_id).with_for_update()
                ).scalar_one_or_none()
                if user_exists is None:
                    continue
                active_count = db.scalar(
                    select(func.count())
                    .select_from(AnalysisTask)
                    .where(
                        AnalysisTask.user_id == task.user_id,
                        AnalysisTask.status == "running",
                        AnalysisTask.lease_expires_at > now,
                    )
                ) or 0
                if active_count >= self.settings.task_user_running_limit:
                    continue
                token = str(uuid4())
                recovering = task.status != "queued"
                claimed = db.execute(update(AnalysisTask).where(
                    AnalysisTask.id == task.id, AnalysisTask.lease_token.is_(None),
                    AnalysisTask.status == task.status,
                ).values(lease_token=token, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS)))
                if claimed.rowcount != 1:
                    db.rollback()
                    continue
                task.status = "cancelled" if task.cancel_requested else "running"
                advance_task_stage(task, "downloading" if task.kind == "purchase_url" else "validating")
                task.attempt_count += 0 if recovering else 1
                task.lease_token = token
                task.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
                task.updated_at = now
                db.commit()
                return task.id, token
            return None

    async def _heartbeat_loop(self, task_id: str, lease_token: str) -> None:
        while True:
            await anyio.sleep(HEARTBEAT_SECONDS)
            with self.sessions() as db:
                changed = db.execute(update(AnalysisTask).where(
                    AnalysisTask.id == task_id, AnalysisTask.lease_token == lease_token,
                    AnalysisTask.status.in_(["running", "cancelled"]), AnalysisTask.lease_expires_at > utc_now(),
                ).values(lease_expires_at=utc_now() + timedelta(seconds=LEASE_SECONDS), updated_at=utc_now()))
                db.commit()
                if changed.rowcount != 1:
                    return

    async def _process(self, task_id: str, lease_token: str) -> None:
        with self.sessions() as db:
            task = db.get(AnalysisTask, task_id, populate_existing=True)
            if task is None or task.lease_token != lease_token:
                return
            unresolved = list(db.execute(select(ProviderOperation).where(
                ProviderOperation.task_id == task.id,
                ProviderOperation.submission_status.in_(["unknown", "submitting", "submitted"]),
            )).scalars())
            if task.cancel_requested and not unresolved:
                self._finish_cancelled(task)
                db.commit()
                return
            if task.kind == "purchase_reanalysis":
                self._process_purchase_reanalysis(db, task, lease_token)
                return
            if task.kind not in {"purchase_url", "purchase_image", "garment_single", "garment_split", "recommendation_item"}:
                self._finish_failed(task, "unsupported_task_kind")
                db.commit()
                return
            user = db.get(User, task.user_id)
            parameters = dict(task.input_parameters or {})
            operation = _latest_operation(db, task.id)
            context = (
                dict(operation.request_parameters or {})
                if operation is not None
                else dict((task.input_refs or {}).get("resolved_context") or {})
            )
            asset_id = str(context.get("asset_id") or (task.input_refs or {}).get("asset_id") or "")
            if user is None:
                self._finish_failed(task, "invalid_task_input")
                db.commit()
                return

            asset: StoredAsset | None = db.get(StoredAsset, asset_id) if asset_id else None
            image_bytes: bytes
            content_type: str
            filename: str
            if task.kind in {"purchase_url", "recommendation_item"} and asset is None:
                recommendation_item = None
                if task.kind == "recommendation_item":
                    item_id = str((task.input_refs or {}).get("recommendation_item_id") or "")
                    recommendation_item = db.get(ShoppingRecommendationItem, item_id)
                    if recommendation_item is None or recommendation_item.user_id != user.id:
                        self._finish_failed(task, "recommendation_item_not_found")
                        db.commit()
                        return
                    url = recommendation_item.image_url
                else:
                    url = str(parameters.get("url") or "")
                if not url:
                    self._finish_failed(task, "invalid_task_input")
                    db.commit()
                    return
                try:
                    if task.kind == "recommendation_item":
                        if self.settings.shopping_recommendation_demo_mode:
                            from app.shopping_recommendations import _product_image_payload
                            image_bytes, content_type = _product_image_payload(self.settings, recommendation_item)
                            source_image_url = url
                        else:
                            downloaded = await download_public_image(url)
                            source_image_url = downloaded.image_url
                            image_bytes = downloaded.image_bytes
                            content_type = downloaded.content_type
                        product_url = recommendation_item.product_url
                        title = recommendation_item.title
                        domain = urlparse(product_url).netloc.lower() or recommendation_item.platform
                        price = recommendation_item.price or None
                    else:
                        extracted = await extract_product_image(url)
                        source_image_url = extracted.source_image_url
                        image_bytes = extracted.image_bytes
                        content_type = extracted.content_type
                        product_url = extracted.product_url
                        title = extracted.title
                        domain = extracted.domain
                        price = extracted.price
                    if recommendation_item is not None:
                        recommendation_item.analysis_status = "analyzing"
                        db.flush()
                    stored_image = create_image_asset(
                        db,
                        self.settings,
                        user.id,
                        image_bytes,
                        content_type,
                    )
                except ProductExtractionError as exc:
                    if task.kind == "recommendation_item" and recommendation_item is not None:
                        recommendation_item.analysis_status = "failed"
                    self._finish_failed(task, exc.code)
                    db.commit()
                    return
                except ImageInputError as exc:
                    if task.kind == "recommendation_item" and recommendation_item is not None:
                        recommendation_item.analysis_status = "failed"
                    self._finish_failed(task, exc.code)
                    db.commit()
                    return
                except Exception:
                    logger.warning("Purchase page extraction failed for task %s", task.id)
                    if task.kind == "recommendation_item" and recommendation_item is not None:
                        recommendation_item.analysis_status = "failed"
                    self._finish_failed(task, "page_fetch_failed")
                    db.commit()
                    return
                asset = stored_image.original
                asset_id = asset.id
                image_bytes = stored_image.prepared.data
                content_type = stored_image.prepared.content_type
                filename = Path(source_image_url).name or "purchase-candidate.jpg"
                context = {
                    "asset_id": asset_id,
                    "product_url": product_url,
                    "source_image_url": source_image_url,
                    "title": title,
                    "domain": domain,
                    "price": price,
                    "recommendation_item_id": str(recommendation_item.id) if recommendation_item else None,
                }
                task.input_refs = {
                    **(task.input_refs or {}),
                    "asset_id": asset_id,
                    "resolved_context": context,
                }
                task.updated_at = utc_now()
                db.commit()
            elif asset is None or asset.user_id != user.id:
                self._finish_failed(task, "asset_not_found")
                db.commit()
                return
            else:
                try:
                    image_bytes = PrivateAssetStorage(self.settings).read(asset.storage_key)
                except OSError:
                    self._finish_failed(task, "asset_unavailable")
                    db.commit()
                    return
                content_type = asset.mime_type
                filename = str(parameters.get("filename") or "garment-image")
                if not context:
                    if task.kind == "purchase_image":
                        product_url = str(parameters.get("product_url") or "")
                        parsed_url = urlparse(product_url)
                        context = {
                            "asset_id": asset_id,
                            "product_url": product_url,
                            "source_image_url": product_url or "manual-upload",
                            "title": Path(filename).stem or "手动上传商品图",
                            "domain": parsed_url.hostname or "manual-upload",
                            "price": None,
                        }
                    else:
                        context = {"asset_id": asset_id}

            if asset is None:
                self._finish_failed(task, "asset_not_found")
                db.commit()
                return

            if task.kind == "garment_split":
                await self._process_garment_split(
                    db,
                    task,
                    lease_token,
                    user,
                    filename,
                    content_type,
                    image_bytes,
                )
                return

            advance_task_stage(task, "recognizing")
            if operation is not None and queryable(operation) and operation.submission_status in {"unknown", "submitting", "submitted"}:
                try:
                    analysis = await self._analyze(db, task, operation, lease_token, filename, content_type, image_bytes)
                    self._assert_lease(db, task, lease_token)
                    operation.result_data = _analysis_to_data(analysis)
                    operation.submission_status = "completed"
                    self._release_operation_slot(db, operation, "provider_completed")
                    db.commit()
                except LostTaskLease:
                    return
                except Exception as exc:
                    self._assert_lease(db, task, lease_token)
                    if isinstance(exc, ProviderTerminalFailure):
                        operation.submission_status = "failed"
                        self._release_operation_slot(db, operation, "provider_confirmed_failure")
                        self._finish_failed(task, "provider_confirmed_failure")
                    else:
                        self._await_provider_resolution(task, error_code="provider_request_failed")
                    db.commit()
                    return
            if operation is not None and operation.submission_status in {"unknown", "submitting", "submitted"}:
                advance_task_stage(task, "awaiting_resolution")
                task.status = "cancelled" if task.cancel_requested else "running"
                task.lease_token = None
                task.lease_expires_at = None
                task.next_attempt_at = None
                db.commit()
                return
            if operation is not None and operation.submission_status == "completed":
                if operation.result_data is None:
                    operation.submission_status = "unknown"
                    advance_task_stage(task, "awaiting_resolution")
                    task.status = "cancelled" if task.cancel_requested else "running"
                    task.lease_token = None
                    task.lease_expires_at = None
                    db.commit()
                    return
                analysis = _analysis_from_data(operation.result_data)
            else:
                generation = operation.generation + 1 if operation is not None else 1
                operation = self._provider_operation(
                    task,
                    image_bytes,
                    content_type,
                    generation,
                    context,
                )
                operation.id = new_id()
                db.add(operation)
                db.flush()
                if not self.settings.ai_demo_mode:
                    slot_id = reserve_provider_slot(
                        db,
                        self.settings.garment_ai_provider,
                        operation.id,
                        capacity=self.settings.provider_concurrency_limit,
                    )
                    if slot_id is None:
                        db.delete(operation)
                        self._defer_for_capacity(task)
                        db.commit()
                        return
                    quota = check_rate_limit(
                        db,
                        "provider_submit",
                        user.id,
                        limit=self.settings.provider_hourly_submission_limit,
                        window_seconds=3600,
                    )
                    if not quota.allowed:
                        release_provider_slot(db, operation.id, "hourly_quota_deferred")
                        db.delete(operation)
                        task.status = "queued"
                        advance_task_stage(task, "recognizing")
                        task.next_attempt_at = quota.reset_at
                        task.lease_token = None
                        task.lease_expires_at = None
                        task.attempt_count = max(0, task.attempt_count - 1)
                        task.updated_at = utc_now()
                        db.commit()
                        return
                    operation.quota_reserved_at = utc_now()
                    operation.slot_reserved_at = utc_now()
                operation.submission_status = "submitting"
                task.updated_at = utc_now()
                db.commit()

                try:
                    analysis = await self._analyze(db, task, operation, lease_token, filename, content_type, image_bytes)
                except AiAnalysisError as exc:
                    self._assert_lease(db, task, lease_token)
                    ambiguous = exc.code in {
                        "provider_timeout",
                        "provider_unavailable",
                        "provider_request_failed",
                    }
                    operation.submission_status = ("submitted" if queryable(operation) else "unknown") if ambiguous else "failed"
                    operation.error_code = exc.code
                    if not ambiguous:
                        release_provider_slot(db, operation.id, "provider_confirmed_failure")
                        operation.slot_released_at = utc_now()
                        operation.slot_release_reason = "provider_confirmed_failure"
                    task.error_code = exc.code
                    if ambiguous:
                        advance_task_stage(task, "awaiting_resolution")
                        task.status = "cancelled" if task.cancel_requested else "running"
                        task.next_attempt_at = None
                    else:
                        task.status = "cancelled" if task.cancel_requested else "failed"
                        advance_task_stage(task, "persisting")
                    task.lease_token = None
                    task.lease_expires_at = None
                    operation.updated_at = utc_now()
                    task.updated_at = utc_now()
                    db.commit()
                    return
                except Exception as exc:
                    self._assert_lease(db, task, lease_token)
                    if isinstance(exc, ProviderTerminalFailure):
                        operation.submission_status = "failed"
                        self._release_operation_slot(db, operation, "provider_confirmed_failure")
                        self._finish_failed(task, "provider_confirmed_failure")
                        db.commit()
                        return
                    operation.submission_status = "submitted" if queryable(operation) else "unknown"
                    operation.error_code = "provider_request_failed"
                    task.error_code = "provider_request_failed"
                    advance_task_stage(task, "awaiting_resolution")
                    task.status = "cancelled" if task.cancel_requested else "running"
                    task.lease_token = None
                    task.lease_expires_at = None
                    operation.updated_at = utc_now()
                    task.updated_at = utc_now()
                    db.commit()
                    return

                task_lease = db.execute(
                    select(AnalysisTask.id).where(
                        AnalysisTask.id == task_id,
                        AnalysisTask.lease_token == lease_token,
                        AnalysisTask.status.in_(["running", "cancelled"]),
                    )
                ).scalar_one_or_none()
                if task_lease is None:
                    return
                operation.submission_status = "completed"
                operation.result_data = _analysis_to_data(analysis)
                release_provider_slot(db, operation.id, "provider_completed")
                operation.slot_released_at = utc_now()
                operation.slot_release_reason = "provider_completed"
                operation.updated_at = utc_now()
                advance_task_stage(task, "scoring")
                task.updated_at = utc_now()
                db.commit()

            task = db.get(AnalysisTask, task_id, populate_existing=True)
            if task is None or task.lease_token != lease_token:
                return
            self._assert_lease(db, task, lease_token)
            if task.cancel_requested:
                self._finish_cancelled(task)
                operation.slot_released_at = utc_now()
                operation.slot_release_reason = "cancelled_after_provider_completion"
                db.commit()
                return

            if task.kind == "garment_single":
                garment = Garment(
                    user_id=user.id,
                    asset_id=asset.id,
                    image_url="",
                    image_key="",
                    thumbnail_url=None,
                    category=analysis.category,
                    colors=analysis.colors,
                    style=analysis.style,
                    material=analysis.material,
                    season=analysis.season,
                    fit=analysis.fit,
                    tags=analysis.tags,
                    ai_result=analysis.raw,
                    ai_confidence=analysis.confidence,
                    status="ready",
                    review_status="pending_review",
                    analysis_source=analysis.analysis_source,
                    provider=analysis.provider,
                    model_version=analysis.model_version,
                    analyzed_at=utc_now(),
                )
                db.add(garment)
                db.flush()
                task.result_refs = [{"resource_type": "garment", "id": garment.id}]
            else:
                garments = list(
                    db.execute(
                        select(Garment).where(
                            Garment.user_id == user.id,
                            Garment.status == "ready",
                            Garment.review_status == "confirmed",
                            Garment.user_confirmed_at.is_not(None),
                        )
                    ).scalars()
                )
                price = _decimal_price(context.get("price"))
                preferences = preference_context(db, user.id)
                occasion = str(context.get("occasion") or ((preferences or {}).get("scenes") or [""])[0] or "")
                feedback_adjustment = matching_feedback_adjustment(
                    db,
                    user.id,
                    style_labels=[analysis.style, *analysis.tags],
                    occasion=occasion or None,
                )
                candidate_id = new_id()
                decision = analyze_purchase(
                    analysis,
                    garments,
                    preferences,
                    actual_price=price,
                    currency="CNY" if price is not None else None,
                    feedback_adjustment=feedback_adjustment,
                    candidate_id=candidate_id, user_id=user.id,
                    excluded_garment_ids=active_excluded_garments(db, user.id),
                )
                candidate = PurchaseCandidate(
                    id=candidate_id,
                    user_id=user.id,
                    asset_id=asset.id,
                    product_url=str(context.get("product_url") or ""),
                    source_image_url=str(context.get("source_image_url") or "manual-upload"),
                    image_url="",
                    image_key="",
                    thumbnail_url=None,
                    title=str(context.get("title") or "商品图片"),
                    domain=str(context.get("domain") or ""),
                    category=analysis.category,
                    colors=analysis.colors,
                    style=analysis.style,
                    material=analysis.material,
                    season=analysis.season,
                    fit=analysis.fit,
                    tags=analysis.tags,
                    ai_result=analysis.raw,
                    ai_confidence=analysis.confidence,
                    similar_items=[item.__dict__ for item in decision.similar_items],
                    recommendation=decision.recommendation,
                    score=decision.score if decision.score is not None else 0,
                    decision_score=decision.score,
                    reason_summary=decision.reason_summary,
                    analysis=decision.analysis,
                    status="ready",
                    analysis_status="ready",
                    analysis_source=analysis.analysis_source,
                    actual_price=price,
                    currency="CNY" if price is not None else None,
                    requested_snapshot_revision=1,
                )
                db.add(candidate)
                db.flush()
                create_purchase_snapshot(db, candidate)
                task.result_refs = [{"resource_type": "purchase_candidate", "id": candidate.id}]
                if task.kind == "recommendation_item":
                    item_id = str(context.get("recommendation_item_id") or "")
                    item = db.get(ShoppingRecommendationItem, item_id)
                    if item is None or item.user_id != user.id:
                        db.delete(candidate)
                        self._finish_failed(task, "recommendation_item_not_found")
                        db.commit()
                        return
                    item.purchase_candidate_id = candidate.id
                    item.recommendation = decision.recommendation
                    item.score = decision.score
                    item.reason_summary = decision.reason_summary
                    item.analysis_status = "analyzed"
            task.status = "succeeded"
            advance_task_stage(task, "persisting")
            task.error_code = None
            task.lease_token = None
            task.lease_expires_at = None
            task.updated_at = utc_now()
            operation.result_refs = list(task.result_refs)
            operation.updated_at = utc_now()
            db.commit()

    def _process_purchase_reanalysis(self, db: Session, task: AnalysisTask, lease_token: str) -> None:
        from app.candidate_ownership import lock_ownership_user
        lock_ownership_user(db, task.user_id)
        parameters = dict(task.input_parameters or {})
        candidate_id = str(parameters.get("candidate_id") or (task.input_refs or {}).get("candidate_id") or "")
        candidate = db.execute(
            select(PurchaseCandidate).where(
                PurchaseCandidate.id == candidate_id,
                PurchaseCandidate.user_id == task.user_id,
            ).with_for_update()
        ).scalar_one_or_none()
        if candidate is None:
            self._finish_failed(task, "candidate_not_found")
            db.commit()
            return
        if task.lease_token != lease_token:
            return
        if candidate.revision != int(parameters.get("candidate_revision") or -1):
            candidate.analysis_status = "ready"
            self._finish_failed(task, "candidate_revision_conflict")
            db.commit()
            return
        garments = list(db.execute(
            select(Garment).where(
                Garment.user_id == task.user_id,
                Garment.status == "ready",
                Garment.review_status == "confirmed",
                Garment.user_confirmed_at.is_not(None),
            )
        ).scalars())
        preferences = preference_context(db, task.user_id)
        analysis = AiAnalysis(
            category=candidate.category,
            colors=candidate.colors or [],
            style=candidate.style,
            material=candidate.material,
            season=candidate.season or [],
            fit=candidate.fit,
            tags=candidate.tags or [],
            confidence=candidate.ai_confidence,
            raw=candidate.ai_result or {},
            analysis_source=candidate.analysis_source,
        )
        occasion = str(((preferences or {}).get("scenes") or [""])[0] or "")
        feedback_adjustment = matching_feedback_adjustment(
            db,
            task.user_id,
            style_labels=[analysis.style, *analysis.tags],
            occasion=occasion or None,
        )
        decision = analyze_purchase(
            analysis,
            garments,
            preferences,
            actual_price=candidate.actual_price,
            currency=candidate.currency,
            feedback_adjustment=feedback_adjustment,
            candidate_id=candidate.id, user_id=task.user_id,
            excluded_garment_ids=active_excluded_garments(db, task.user_id) | ({candidate.owned_garment_id} if candidate.owned_garment_id else set()),
        )
        snapshot_revision = int(parameters.get("snapshot_revision") or candidate.requested_snapshot_revision)
        payload = {
            "recommendation": decision.recommendation,
            "score": decision.score,
            "reason_summary": decision.reason_summary,
            "analysis": decision.analysis,
            "actual_price": str(candidate.actual_price) if candidate.actual_price is not None else None,
            "currency": candidate.currency,
            "revision": candidate.revision,
        }
        if snapshot_revision == candidate.requested_snapshot_revision:
            candidate.similar_items = [item.__dict__ for item in decision.similar_items]
            candidate.recommendation = decision.recommendation
            candidate.score = decision.score if decision.score is not None else 0
            candidate.decision_score = decision.score
            candidate.reason_summary = decision.reason_summary
            candidate.analysis = decision.analysis
            candidate.analysis_status = "ready"
            candidate.updated_at = utc_now()
        create_purchase_snapshot(
            db,
            candidate,
            snapshot_revision=snapshot_revision,
            decision_payload=payload,
        )
        task.status = "succeeded"
        advance_task_stage(task, "persisting")
        task.error_code = None
        task.result_refs = [{"resource_type": "purchase_candidate", "id": candidate.id}]
        task.lease_token = None
        task.lease_expires_at = None
        task.updated_at = utc_now()
        db.commit()

    async def _process_garment_split(
        self,
        db: Session,
        task: AnalysisTask,
        lease_token: str,
        user: User,
        filename: str,
        content_type: str,
        image_bytes: bytes,
    ) -> None:
        split_op = _latest_operation(db, task.id, "split:0")
        if split_op is not None and split_op.submission_status in {"unknown", "submitting", "submitted"} and not queryable(split_op):
            self._await_provider_resolution(task)
            db.commit()
            return

        outputs: list[dict[str, object]] = []
        if split_op is not None and split_op.submission_status == "completed" and split_op.result_data:
            outputs = list(split_op.result_data.get("outputs") or [])
        else:
            provider = split_op.provider if split_op is not None and queryable(split_op) else self.settings.workflow_provider
            if provider not in {"demo", "comfyui", "runninghub"}:
                self._finish_failed(task, "split_provider_not_configured")
                db.commit()
                return
            if provider == "demo" and not self.settings.ai_demo_mode:
                self._finish_failed(task, "split_provider_not_configured")
                db.commit()
                return
            split_op = self._prepare_operation(
                db,
                task,
                "split:0",
                image_bytes,
                content_type,
                {"filename": filename, "workflow_provider": provider},
                provider=provider,
                reserve_capacity=provider != "demo",
            )
            if split_op is None:
                return
            if split_op.submission_status == "completed" and split_op.result_data:
                outputs = list(split_op.result_data.get("outputs") or [])
            else:
                try:
                    if provider in {"runninghub", "comfyui"}:
                        outputs = await self._remote_split(db, task, split_op, lease_token, filename, content_type, image_bytes)
                    else:
                        outputs = await self._split_outputs(provider, filename, content_type, image_bytes)
                    self._assert_lease(db, task, lease_token)
                    if not outputs:
                        raise ValueError("no_garments_detected")
                    stored_outputs: list[dict[str, object]] = []
                    for index, item in enumerate(outputs, start=1):
                        crop_box = item.get("crop_box") if isinstance(item.get("crop_box"), dict) else None
                        source = item.get("data")
                        if not isinstance(source, bytes):
                            raise ValueError("invalid_workflow_output")
                        cropped = _crop_image(source, crop_box)
                        crop_type = _image_content_type(cropped)
                        stored = create_image_asset(db, self.settings, user.id, cropped, crop_type)
                        stored_outputs.append({
                            "index": index,
                            "asset_id": stored.original.id,
                            "category": str(item.get("category") or ""),
                            "crop_box": crop_box,
                            "filename": str(item.get("filename") or f"garment-{index}"),
                            "workflow_metadata": dict(item.get("metadata") or {}),
                        })
                    outputs = stored_outputs
                    split_op = db.get(ProviderOperation, split_op.id)
                    split_op.result_data = {"outputs": outputs}
                    split_op.submission_status = "completed"
                    split_op.updated_at = utc_now()
                    self._release_operation_slot(db, split_op, "provider_completed")
                    advance_task_stage(task, "scoring")
                    task.updated_at = utc_now()
                    db.commit()
                except Exception as exc:
                    self._assert_lease(db, task, lease_token)
                    operation = db.get(ProviderOperation, split_op.id)
                    if operation is not None:
                        ambiguous = not isinstance(exc, ValueError)
                        operation.submission_status = ("submitted" if queryable(operation) else "unknown") if ambiguous else "failed"
                        operation.error_code = "provider_request_failed" if ambiguous else "split_output_invalid"
                        operation.updated_at = utc_now()
                        if not ambiguous:
                            self._release_operation_slot(db, operation, "provider_confirmed_failure")
                    code = "provider_request_failed" if not isinstance(exc, ValueError) else str(exc)
                    if code not in {"provider_request_failed", "provider_confirmed_failure", "split_output_invalid", "no_garments_detected", "invalid_workflow_output"}:
                        code = "split_output_invalid"
                    if code == "provider_request_failed":
                        self._await_provider_resolution(task, error_code=code)
                    else:
                        self._finish_failed(task, code)
                    db.commit()
                    return

        task = db.get(AnalysisTask, task.id, populate_existing=True)
        if task is None or task.lease_token != lease_token:
            return
        existing_refs = list(task.result_refs or [])
        output_count = min(len(outputs), 12)
        for index, output in enumerate(outputs[:output_count], start=1):
            task = db.get(AnalysisTask, task.id, populate_existing=True)
            if task is None or task.lease_token != lease_token:
                return
            if task.cancel_requested:
                self._finish_cancelled(task)
                db.commit()
                return
            asset_id = str(output.get("asset_id") or "")
            asset = db.get(StoredAsset, asset_id)
            if asset is None or asset.user_id != user.id:
                self._finish_failed(task, "asset_not_found")
                db.commit()
                return
            try:
                crop_bytes = PrivateAssetStorage(self.settings).read(asset.storage_key)
            except OSError:
                self._finish_failed(task, "asset_unavailable")
                db.commit()
                return
            operation_key = f"tag:{index}"
            tag_op = _latest_operation(db, task.id, operation_key)
            if tag_op is not None and tag_op.submission_status in {"unknown", "submitting", "submitted"} and not queryable(tag_op):
                self._await_provider_resolution(task)
                db.commit()
                return
            if tag_op is not None and tag_op.submission_status == "completed" and tag_op.result_data:
                analysis = _analysis_from_data(tag_op.result_data)
            else:
                tag_op = self._prepare_operation(
                    db,
                    task,
                    operation_key,
                    crop_bytes,
                    asset.mime_type,
                    {"asset_id": asset.id, "index": index},
                    provider=self.settings.garment_ai_provider,
                    reserve_capacity=not self.settings.ai_demo_mode,
                )
                if tag_op is None:
                    return
                if tag_op.submission_status == "completed" and tag_op.result_data:
                    analysis = _analysis_from_data(tag_op.result_data)
                else:
                    try:
                        analysis = await self._analyze(db, task, tag_op, lease_token, str(output.get("filename") or filename), asset.mime_type, crop_bytes)
                    except AiAnalysisError as exc:
                        self._assert_lease(db, task, lease_token)
                        op = db.get(ProviderOperation, tag_op.id)
                        ambiguous = exc.code in {"provider_timeout", "provider_unavailable", "provider_request_failed"}
                        op.submission_status = ("submitted" if queryable(op) else "unknown") if ambiguous else "failed"
                        op.error_code = exc.code
                        op.updated_at = utc_now()
                        task.error_code = exc.code
                        if ambiguous:
                            self._await_provider_resolution(task, error_code=exc.code)
                        else:
                            self._release_operation_slot(db, op, "provider_confirmed_failure")
                            self._finish_failed(task, exc.code)
                        db.commit()
                        return
                    except Exception as exc:
                        self._assert_lease(db, task, lease_token)
                        if isinstance(exc, ProviderTerminalFailure):
                            tag_op.submission_status = "failed"
                            self._release_operation_slot(db, tag_op, "provider_confirmed_failure")
                            self._finish_failed(task, "provider_confirmed_failure")
                            db.commit()
                            return
                        op = db.get(ProviderOperation, tag_op.id)
                        op.submission_status = "submitted" if queryable(op) else "unknown"
                        op.error_code = "provider_request_failed"
                        self._await_provider_resolution(task, error_code="provider_request_failed")
                        db.commit()
                        return
                    self._assert_lease(db, task, lease_token)
                    op = db.get(ProviderOperation, tag_op.id)
                    op.result_data = _analysis_to_data(analysis)
                    op.submission_status = "completed"
                    op.updated_at = utc_now()
                    self._release_operation_slot(db, op, "provider_completed")
                    db.commit()

            task = db.get(AnalysisTask, task.id, populate_existing=True)
            if task is None or task.lease_token != lease_token:
                return
            self._assert_lease(db, task, lease_token)
            if task.cancel_requested:
                self._finish_cancelled(task)
                db.commit()
                return
            tag_op = _latest_operation(db, task.id, operation_key)
            garment_ref = next(
                (item for item in (tag_op.result_refs or []) if item.get("resource_type") == "garment"),
                None,
            ) if tag_op else None
            garment = db.get(Garment, garment_ref["id"]) if garment_ref else None
            if garment is None:
                raw = dict(analysis.raw)
                raw.update(dict(output.get("workflow_metadata") or {}))
                detected_category = str(output.get("category") or "")
                valid_categories = {"top", "bottom", "outerwear", "dress", "shoes", "bag", "accessory"}
                category = detected_category if detected_category in valid_categories else analysis.category
                raw.update({
                    "detected_category": category,
                    "crop_box": output.get("crop_box"),
                    "workflow_provider": self.settings.workflow_provider,
                })
                garment = Garment(
                    user_id=user.id,
                    asset_id=asset.id,
                    image_url="",
                    image_key="",
                    thumbnail_url=None,
                    category=category,
                    colors=analysis.colors,
                    style=analysis.style,
                    material=analysis.material,
                    season=analysis.season,
                    fit=analysis.fit,
                    tags=analysis.tags,
                    crop_box=output.get("crop_box"),
                    ai_result=raw,
                    ai_confidence=analysis.confidence,
                    status="ready",
                    review_status="pending_review",
                    analysis_source=analysis.analysis_source,
                    provider=analysis.provider,
                    model_version=analysis.model_version,
                    analyzed_at=utc_now(),
                )
                db.add(garment)
                db.flush()
                tag_op.result_refs = [{"resource_type": "garment", "id": garment.id}]
                task.result_refs = [
                    *[item for item in task.result_refs or [] if item.get("id") != garment.id],
                    {"resource_type": "garment", "id": garment.id},
                ]
                db.commit()

        task = db.get(AnalysisTask, task.id, populate_existing=True)
        if task is None or task.lease_token != lease_token:
            return
        task.status = "succeeded"
        advance_task_stage(task, "persisting")
        task.error_code = None
        task.lease_token = None
        task.lease_expires_at = None
        task.updated_at = utc_now()
        db.commit()

    @staticmethod
    def _assert_lease(db, task, lease_token):
        owned = db.execute(select(AnalysisTask.id).where(
            AnalysisTask.id == task.id, AnalysisTask.lease_token == lease_token,
            AnalysisTask.status.in_(["running", "cancelled"]), AnalysisTask.lease_expires_at > utc_now(),
        ).with_for_update()).scalar_one_or_none()
        if owned is None:
            db.rollback()
            raise LostTaskLease("task_lease_lost")
        db.refresh(task)

    def _schedule_recovery(self, task_id):
        with self.sessions() as db:
            task = db.execute(select(AnalysisTask).where(AnalysisTask.id == task_id).with_for_update()).scalar_one_or_none()
            if task is None or task.status not in {"running", "cancelled"} or task.lease_token or task.stage != "awaiting_resolution":
                return
            operations = list(db.execute(select(ProviderOperation).where(
                ProviderOperation.task_id == task_id,
                ProviderOperation.submission_status.in_(["unknown", "submitting", "submitted"]),
            )).scalars())
            now = utc_now()
            if operations and all(queryable(op) for op in operations):
                started = min(recovery_time((op.reconciliation_evidence or {}).get("accepted_at") or op.created_at) for op in operations)
                if (now - started).total_seconds() >= 900:
                    task.error_code = "provider_recovery_timeout"
                    task.next_attempt_at = None
                else:
                    task.next_attempt_at = now + timedelta(seconds=30)
            db.commit()

    async def _accepted_query(self, db, task, op, lease_token, submit, collect):
        if not op.provider_task_id:
            remote_id = await submit()
            self._assert_lease(db, task, lease_token)
            op.provider_task_id = remote_id
            op.submission_status = "submitted"
            op.reconciliation_evidence = {**(op.reconciliation_evidence or {}), "accepted_at": utc_now().isoformat()}
            db.commit()
        self._assert_lease(db, task, lease_token)
        evidence = dict(op.reconciliation_evidence or {})
        started = recovery_time(evidence.get("accepted_at") or op.created_at)
        manual = evidence.pop("manual_query", False)
        if not manual and (utc_now() - started).total_seconds() >= 900:
            raise TimeoutError("provider_recovery_timeout")
        last_query = evidence.get("last_query_at")
        if last_query and (utc_now() - recovery_time(last_query)).total_seconds() < 30:
            raise ProviderPending("reconciliation_rate_limited")
        evidence["last_query_at"] = utc_now().isoformat()
        op.reconciliation_evidence = evidence
        db.commit()
        result = await collect(op.provider_task_id)
        self._assert_lease(db, task, lease_token)
        return result

    async def _remote_split(self, db, task, op, lease_token, filename, content_type, image_bytes):
        client = RunningHubClient(self.settings) if op.provider == "runninghub" else ComfyUIClient(self.settings)
        outputs = await self._accepted_query(db, task, op, lease_token,
            lambda: client.submit_garment_recognition(filename, content_type, image_bytes),
            client.collect_garment_recognition)
        normalized = []
        for item in outputs[:12]:
            metadata = {"provider": op.provider, f"{op.provider}_filename": item.filename}
            if op.provider == "comfyui":
                metadata.update({"comfyui_type": item.type, "comfyui_subfolder": item.subfolder})
            else:
                metadata.update({"runninghub_file_url": item.file_url, "runninghub_node_id": item.node_id})
            normalized.append({"data": item.data, "category": getattr(item, "category", ""),
                               "filename": item.filename, "metadata": metadata})
        return normalized

    async def _analyze(self, db, task, op, lease_token, filename, content_type, image_bytes):
        if op.provider != "runninghub" or (self.settings.ai_demo_mode and not op.provider_task_id):
            return await AiService(self.settings).analyze_garment(filename=filename, content_type=content_type, image_bytes=image_bytes)
        from app.ai import _garment_tagging_prompt, _complete_vl_payload, _extract_runninghub_text_result, _analysis_from_vl_payload
        client = RunningHubClient(self.settings)
        async def submit():
            workflow = RunningHubWorkflow.from_file(AiService(self.settings)._runninghub_garment_workflow_path())
            inputs = {key: _garment_tagging_prompt() for key in ("prompt", "text") if key in workflow.inputs}
            return await client.submit_workflow(workflow, inputs, {"image": (filename, content_type, image_bytes)})
        result = await self._accepted_query(db, task, op, lease_token, submit, client.collect_workflow)
        return _analysis_from_vl_payload(_complete_vl_payload(json.loads(_extract_runninghub_text_result(result))), provider="runninghub")

    async def _split_outputs(
        self,
        provider: str,
        filename: str,
        content_type: str,
        image_bytes: bytes,
    ) -> list[dict[str, object]]:
        if provider == "demo":
            detections = await AiService(self.settings).detect_garments(filename, content_type, image_bytes)
            return [
                {
                    "data": image_bytes,
                    "category": detection.category,
                    "crop_box": detection.crop_box,
                    "filename": filename,
                }
                for detection in detections
            ]
        raise ValueError("remote_split_requires_provider_operation")

    def _prepare_operation(
        self,
        db: Session,
        task: AnalysisTask,
        operation_key: str,
        image_bytes: bytes,
        content_type: str,
        context: dict[str, object],
        *,
        provider: str,
        reserve_capacity: bool,
    ) -> ProviderOperation | None:
        latest = _latest_operation(db, task.id, operation_key)
        if latest is not None and (latest.submission_status == "completed" or queryable(latest) and latest.submission_status in {"unknown", "submitted", "submitting"}):
            return latest
        generation = latest.generation + 1 if latest is not None else 1
        operation = self._provider_operation(
            task,
            image_bytes,
            content_type,
            generation,
            context,
            operation_key=operation_key,
            provider=provider,
        )
        operation.id = new_id()
        db.add(operation)
        db.flush()
        if reserve_capacity:
            slot = reserve_provider_slot(
                db,
                provider,
                operation.id,
                capacity=self.settings.provider_concurrency_limit,
            )
            if slot is None:
                db.delete(operation)
                self._defer_for_capacity(task)
                db.commit()
                return None
            quota = check_rate_limit(
                db,
                "provider_submit",
                task.user_id,
                limit=self.settings.provider_hourly_submission_limit,
                window_seconds=3600,
            )
            if not quota.allowed:
                release_provider_slot(db, operation.id, "hourly_quota_deferred")
                db.delete(operation)
                task.status = "queued"
                advance_task_stage(task, "recognizing")
                task.next_attempt_at = quota.reset_at
                task.lease_token = None
                task.lease_expires_at = None
                task.attempt_count = max(0, task.attempt_count - 1)
                task.updated_at = utc_now()
                db.commit()
                return None
            operation.quota_reserved_at = utc_now()
            operation.slot_reserved_at = utc_now()
        operation.submission_status = "submitting"
        operation.updated_at = utc_now()
        db.commit()
        return operation

    @staticmethod
    def _release_operation_slot(db: Session, operation: ProviderOperation, reason: str) -> None:
        if operation.slot_reserved_at is None or operation.slot_released_at is not None:
            return
        release_provider_slot(db, operation.id, reason)
        operation.slot_released_at = utc_now()
        operation.slot_release_reason = reason

    @staticmethod
    def _await_provider_resolution(task: AnalysisTask, *, error_code: str | None = None) -> None:
        advance_task_stage(task, "awaiting_resolution")
        task.status = "cancelled" if task.cancel_requested else "running"
        task.error_code = error_code or task.error_code
        task.lease_token = None
        task.lease_expires_at = None
        task.next_attempt_at = None
        task.updated_at = utc_now()

    def _provider_operation(
        self,
        task: AnalysisTask,
        image_bytes: bytes,
        content_type: str,
        generation: int,
        context: dict[str, object],
        *,
        operation_key: str = "tag:0",
        provider: str | None = None,
    ) -> ProviderOperation:
        selected_provider = provider or self.settings.garment_ai_provider
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "image_sha256": hashlib.sha256(image_bytes).hexdigest(),
                    "content_type": content_type,
                    "provider": selected_provider,
                    "model": self.settings.ai_model,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        return ProviderOperation(
            task_id=task.id,
            operation_key=operation_key,
            generation=generation,
            provider=selected_provider,
            request_fingerprint=fingerprint,
            request_parameters={
                **context,
                "content_type": content_type,
                "model": self.settings.ai_model,
            },
        )

    @staticmethod
    def _finish_failed(task: AnalysisTask, code: str) -> None:
        task.status = "cancelled" if task.cancel_requested else "failed"
        advance_task_stage(task, "persisting")
        task.error_code = code
        task.lease_token = None
        task.lease_expires_at = None
        task.updated_at = utc_now()

    @staticmethod
    def _finish_cancelled(task: AnalysisTask) -> None:
        task.status = "cancelled"
        advance_task_stage(task, "persisting")
        task.lease_token = None
        task.lease_expires_at = None
        task.updated_at = utc_now()

    def _defer_for_capacity(self, task: AnalysisTask) -> None:
        task.status = "queued"
        advance_task_stage(task, "recognizing")
        task.next_attempt_at = utc_now() + timedelta(
            seconds=self.settings.task_capacity_retry_delay_seconds
        )
        task.lease_token = None
        task.lease_expires_at = None
        task.attempt_count = max(0, task.attempt_count - 1)
        task.updated_at = utc_now()


def _crop_image(data: bytes, crop_box: dict[str, int] | None) -> bytes:
    if not crop_box:
        return data
    from io import BytesIO

    from PIL import Image

    with Image.open(BytesIO(data)) as image:
        left = max(0, int(crop_box.get("x", 0)))
        top = max(0, int(crop_box.get("y", 0)))
        right = min(image.width, left + max(1, int(crop_box.get("width", image.width))))
        bottom = min(image.height, top + max(1, int(crop_box.get("height", image.height))))
        if right <= left or bottom <= top:
            raise ValueError("invalid_crop_box")
        cropped = image.crop((left, top, right, bottom))
        output = BytesIO()
        if cropped.mode in {"RGBA", "LA"} or "transparency" in cropped.info:
            cropped.save(output, format="PNG", optimize=True)
        else:
            cropped.convert("RGB").save(output, format="JPEG", quality=92)
        return output.getvalue()


def _image_content_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def _image_suffix(content_type: str) -> str:
    return {"image/png": ".png", "image/webp": ".webp"}.get(content_type, ".jpg")


def _decimal_price(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return amount if amount.is_finite() and amount >= 0 else None


def _analysis_to_data(analysis: AiAnalysis) -> dict[str, object]:
    return {
        "category": analysis.category,
        "colors": analysis.colors,
        "style": analysis.style,
        "material": analysis.material,
        "season": analysis.season,
        "fit": analysis.fit,
        "tags": analysis.tags,
        "confidence": analysis.confidence,
        "raw": analysis.raw,
        "analysis_source": analysis.analysis_source,
        "provider": analysis.provider,
        "model_version": analysis.model_version,
        "provider_usage": analysis.provider_usage,
        "reported_cost": analysis.reported_cost,
    }


def _analysis_from_data(value: dict[str, object]) -> AiAnalysis:
    return AiAnalysis(
        category=str(value["category"]),
        colors=[str(item) for item in value.get("colors", [])],
        style=str(value.get("style", "")),
        material=str(value.get("material", "")),
        season=[str(item) for item in value.get("season", [])],
        fit=str(value.get("fit", "")),
        tags=[str(item) for item in value.get("tags", [])],
        confidence=float(value.get("confidence", 0)),
        raw=dict(value.get("raw", {})),
        analysis_source=str(value.get("analysis_source", "legacy_unknown")),
        provider=str(value["provider"]) if value.get("provider") else None,
        model_version=str(value["model_version"]) if value.get("model_version") else None,
        provider_usage={key: count for key, count in value["provider_usage"].items() if isinstance(count, int) and not isinstance(count, bool)} if isinstance(value.get("provider_usage"), dict) else None,
        reported_cost={key: str(part) for key, part in value["reported_cost"].items()} if isinstance(value.get("reported_cost"), dict) else None,
    )


def _latest_operation(db: Session, task_id: str, operation_key: str = "tag:0") -> ProviderOperation | None:
    return db.execute(
        select(ProviderOperation)
        .where(ProviderOperation.task_id == task_id, ProviderOperation.operation_key == operation_key)
        .order_by(ProviderOperation.generation.desc())
    ).scalars().first()


async def _serve(worker: TaskWorker) -> None:
    while True:
        if not await worker.run_once():
            await anyio.sleep(1)


def main() -> None:
    init_db()
    settings = get_settings()
    worker = TaskWorker(get_session_factory(), settings)
    asyncio.run(_serve(worker))


if __name__ == "__main__":
    main()
