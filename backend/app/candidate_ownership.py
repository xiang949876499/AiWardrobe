"""Canonical candidate ownership writes shared by v2 and compatibility routes."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.decision_context import bump_revision
from app.models import CandidateOwnershipOperation, Garment, PurchaseCandidate, User, utc_now


@dataclass
class OwnershipResult:
    garment: Garment
    payload: dict[str, object]
    created: bool = False


def lock_ownership_user(db: Session, user_id: str) -> None:
    if db.get_bind().dialect.name == "sqlite":
        connection = db.connection()
        # SQLite ignores FOR UPDATE. Reserve its writer before reading either side
        # so concurrent independent sessions cannot both see an unbound garment.
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
    # A shared first lock gives save/link/delete a consistent lock order, including
    # garments that do not yet have a candidate relationship.
    db.execute(select(User.id).where(User.id == user_id).with_for_update()).scalar_one()


def _candidate(db: Session, user_id: str, candidate_id: str) -> PurchaseCandidate:
    lock_ownership_user(db, user_id)
    candidate = db.execute(select(PurchaseCandidate).where(
        PurchaseCandidate.id == candidate_id, PurchaseCandidate.user_id == user_id,
    ).with_for_update()).scalar_one_or_none()
    if candidate is None:
        raise HTTPException(404, detail={"code": "candidate_not_found"})
    return candidate


def _existing(db: Session, user_id: str, candidate: PurchaseCandidate, *,
              allow_reconciliation: bool = False) -> Garment | None:
    if candidate.ownership_state == "removed":
        raise HTTPException(409, detail={"code": "owned_garment_removed"})
    garment = db.execute(select(Garment).where(
        Garment.user_id == user_id, Garment.source_candidate_id == candidate.id,
    ).with_for_update()).scalar_one_or_none()
    if candidate.ownership_state == "linked" and garment is None and not allow_reconciliation:
        # Ambiguous historical references are never treated as permission to create.
        raise HTTPException(409, detail={"code": "ownership_link_required"})
    return garment


def _tags(tags: list[str]) -> list[str]:
    return sorted({tag.strip() for tag in tags if tag.strip()})


def _replay(db: Session, user_id: str, operation: str, key: str | None,
            semantic_input: dict[str, object]) -> tuple[CandidateOwnershipOperation | None, str | None, str]:
    digest = sha256(json.dumps(semantic_input, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if key is None:
        return None, None, digest
    try:
        key = str(UUID(key))
    except ValueError as exc:
        raise HTTPException(422, detail={"code": "invalid_idempotency_key"}) from exc
    previous = db.execute(select(CandidateOwnershipOperation).where(
        CandidateOwnershipOperation.user_id == user_id,
        CandidateOwnershipOperation.operation == operation,
        CandidateOwnershipOperation.idempotency_key == key,
    )).scalar_one_or_none()
    if previous is not None and previous.input_digest != digest:
        raise HTTPException(409, detail={"code": "idempotency_conflict"})
    return previous, key, digest


def _result(db: Session, user_id: str, operation: str, key: str | None, digest: str,
            candidate: PurchaseCandidate, garment: Garment, *, created: bool = False) -> OwnershipResult:
    payload = {"garment_id": garment.id, "candidate_id": candidate.id,
               "ownership_state": "linked", "revision": candidate.revision}
    if key is not None:
        db.add(CandidateOwnershipOperation(user_id=user_id, operation=operation,
            idempotency_key=key, input_digest=digest, response=payload))
    db.commit()
    return OwnershipResult(garment, payload, created)


def _check_revision(candidate: PurchaseCandidate, expected: int | None) -> None:
    if expected is not None and candidate.revision != expected:
        raise HTTPException(409, detail={"code": "revision_conflict", "current_revision": candidate.revision})


def save_owned_candidate(db: Session, user_id: str, candidate_id: str, *,
                         expected_revision: int | None = None, confirm_owned: bool = True,
                         tags: list[str] | None = None, idempotency_key: str | None = None) -> OwnershipResult:
    candidate = _candidate(db, user_id, candidate_id)
    existing = _existing(db, user_id, candidate)
    previous, key, digest = _replay(db, user_id, "save", idempotency_key, {
        "candidate_id": candidate_id, "confirm_owned": confirm_owned,
        "tags": _tags(tags) if tags is not None else None,
    })
    if previous is not None and existing is not None:
        return OwnershipResult(existing, previous.response)
    if existing is not None:
        return _result(db, user_id, "save", key, digest, candidate, existing)
    if candidate.ownership_state == "legacy_unlinked":
        raise HTTPException(409, detail={"code": "ownership_link_required"})
    if not confirm_owned:
        raise HTTPException(422, detail={"code": "ownership_confirmation_required"})
    _check_revision(candidate, expected_revision)
    garment = Garment(user_id=user_id, asset_id=candidate.asset_id, image_url="", image_key="",
        category=candidate.category, colors=candidate.colors or [], style=candidate.style,
        material=candidate.material, season=candidate.season or [], fit=candidate.fit,
        tags=_tags(tags if tags is not None else candidate.tags or []),
        ai_result=candidate.ai_result or {}, ai_confidence=candidate.ai_confidence,
        status="ready", review_status="confirmed", analysis_source=candidate.analysis_source,
        user_confirmed_at=utc_now(), source_candidate_id=candidate.id)
    db.add(garment)
    db.flush()
    candidate.status = "saved"
    candidate.ownership_state = "linked"
    candidate.owned_garment_id = garment.id
    candidate.revision += 1
    candidate.updated_at = utc_now()
    bump_revision(db, user_id, "wardrobe_revision")
    return _result(db, user_id, "save", key, digest, candidate, garment, created=True)


def link_owned_candidate(db: Session, user_id: str, candidate_id: str, garment_id: str, *,
                         expected_revision: int, confirm_owned: bool, idempotency_key: str) -> OwnershipResult:
    candidate = _candidate(db, user_id, candidate_id)
    existing = _existing(db, user_id, candidate, allow_reconciliation=True)
    previous, key, digest = _replay(db, user_id, "link", idempotency_key, {
        "candidate_id": candidate_id, "garment_id": garment_id, "confirm_owned": confirm_owned,
    })
    if previous is not None and existing is not None:
        return OwnershipResult(existing, previous.response)
    if not confirm_owned:
        raise HTTPException(422, detail={"code": "ownership_confirmation_required"})
    if existing is not None:
        if existing.id != garment_id:
            raise HTTPException(409, detail={"code": "candidate_already_linked"})
        return _result(db, user_id, "link", key, digest, candidate, existing)
    _check_revision(candidate, expected_revision)
    garment = db.execute(select(Garment).where(
        Garment.id == garment_id, Garment.user_id == user_id, Garment.status == "ready",
        Garment.review_status == "confirmed", Garment.user_confirmed_at.is_not(None),
    ).with_for_update()).scalar_one_or_none()
    if garment is None:
        raise HTTPException(404, detail={"code": "garment_not_found"})
    if garment.source_candidate_id is not None and garment.source_candidate_id != candidate.id:
        raise HTTPException(409, detail={"code": "garment_already_linked"})
    # Inconsistent pre-repair mirrors require explicit reconciliation; don't guess
    # that this row is available merely because the canonical column is empty.
    other = db.execute(select(PurchaseCandidate.id).where(
        PurchaseCandidate.id != candidate.id, PurchaseCandidate.owned_garment_id == garment.id,
        PurchaseCandidate.ownership_state == "linked",
    )).first()
    if other is not None:
        raise HTTPException(409, detail={"code": "garment_already_linked"})
    garment.source_candidate_id = candidate.id
    candidate.owned_garment_id = garment.id
    candidate.ownership_state = "linked"
    candidate.status = "saved"
    candidate.decision_status = "bought"
    candidate.revision += 1
    candidate.updated_at = utc_now()
    return _result(db, user_id, "link", key, digest, candidate, garment)
