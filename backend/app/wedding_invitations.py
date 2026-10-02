"""Token-in-body disclosure and one-use access recovery without regranting consent."""
import hashlib
import secrets
from datetime import timedelta
import jwt
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from app.config import get_settings
from app.database import get_db
from app.models import ConsentInvite, WeddingParticipant, WeddingProject, utc_now
from app.schemas import ConsentConfirmRequest
from app.security import hash_code
from app.wedding_service import get_wedding_actor, require_project_owner, _as_utc

router = APIRouter(tags=['wedding invitations'])
DELETION = ('Revoke consent in this project at any time. Product access stops immediately; '
    'primary storage deletion within 24 hours, backup rotation within 30 days. '
    'Already downloaded files and supplier copies follow the supplier deletion process. '
    'No advertising, public display or training permission is granted.')


def disclosure(invite, person):
    return invite.disclosure or dict(project_id=invite.project_id, person_key=person.person_key,
        display_name=person.display_name, kind=invite.kind, terms_version=invite.terms_version,
        provider_list=invite.provider_list, scope=invite.scope,
        purposes=['Create this wedding film', 'Private participant portrait and preview review', 'Private storage for 30 days'],
        deletion=DELETION, expires_at=_as_utc(invite.expires_at).isoformat())


def disclosure_hash(invite, person):
    from app.wedding_creation import digest
    return digest(disclosure(invite, person))


def lookup(db, token):
    invite = db.scalar(select(ConsentInvite).where(ConsentInvite.token_hash == hash_code(token)))
    if not invite:
        raise HTTPException(404, 'CONSENT_INVITE_NOT_FOUND')
    if invite.status != 'active' or invite.consumed_at or invite.revoked_at:
        raise HTTPException(409, 'CONSENT_INVITE_ALREADY_USED')
    if _as_utc(invite.expires_at) <= utc_now():
        raise HTTPException(410, 'CONSENT_INVITE_EXPIRED')
    return invite


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)


class ConfirmRequest(ConsentConfirmRequest, TokenRequest):
    pass


@router.post('/consent-invites/preview')
def preview(body: TokenRequest, db=Depends(get_db)):
    invite = lookup(db, body.token)
    person = db.get(WeddingParticipant, invite.participant_id)
    return {**disclosure(invite, person), 'disclosure_hash':disclosure_hash(invite, person)}


@router.post('/wedding-projects/{project_id}/access-invites', status_code=201)
def access_invite(project_id: str, actor=Depends(get_wedding_actor), db=Depends(get_db)):
    require_project_owner(db, actor, project_id)
    from app.wedding_retention import lock_project
    project = lock_project(db, project_id)
    person = db.scalar(select(WeddingParticipant).where(WeddingParticipant.project_id == project.id,
        WeddingParticipant.person_key == 'B'))
    if not person or person.consent_status != 'active' or person.revoked_at:
        raise HTTPException(409, 'ACTIVE_CONSENT_REQUIRED')
    db.execute(update(ConsentInvite).where(ConsentInvite.participant_id == person.id,
        ConsentInvite.status == 'active').values(status='revoked', revoked_at=utc_now()))
    raw = secrets.token_urlsafe(32)
    invite = ConsentInvite(project_id=project.id, participant_id=person.id, token_hash=hash_code(raw),
        kind='access', scope=person.consent_scope, provider_list=person.provider_list,
        terms_version=person.terms_version, expires_at=utc_now()+timedelta(days=7))
    invite.disclosure = disclosure(invite, person)
    db.add(invite); db.commit()
    return dict(id=invite.id, invite_token=raw, person_key='B', expires_at=invite.expires_at)


def redeem_access(db, invite, person, body):
    if person.consent_status != 'active' or person.revoked_at or person.terms_version != invite.terms_version:
        raise HTTPException(409, 'ACTIVE_CONSENT_CHANGED')
    if not body.accepted:
        raise HTTPException(409, 'ACCESS_ACCEPTANCE_REQUIRED')
    claimed = db.execute(update(ConsentInvite).where(ConsentInvite.id == invite.id,
        ConsentInvite.status == 'active', ConsentInvite.expires_at > utc_now()).values(status='consumed', consumed_at=utc_now()).execution_options(synchronize_session=False))
    if claimed.rowcount != 1:
        raise HTTPException(409, 'CONSENT_INVITE_ALREADY_USED')
    cfg = get_settings(); now = utc_now(); expires = now+timedelta(hours=24)
    token = jwt.encode(dict(sub=person.id,participant_id=person.id,project_id=person.project_id,
        token_type='wedding_participant',jti=secrets.token_urlsafe(24),iat=int(now.timestamp()),exp=int(expires.timestamp())),
        cfg.jwt_secret,algorithm=cfg.jwt_algorithm)
    person.session_token_hash = hashlib.sha256(token.encode()).hexdigest()
    person.session_expires_at = expires
    db.commit()
    return dict(access_token=token,accepted=True,project_id=person.project_id,
        participant=dict(person_key=person.person_key,display_name=person.display_name,
            consent_status=person.consent_status,confirmed_at=person.confirmed_at))


@router.post('/consent-invites/confirm')
def confirm(body: ConfirmRequest, db=Depends(get_db)):
    from app.routers.wedding import confirm_consent_invite
    return confirm_consent_invite(body.token, body, db)
