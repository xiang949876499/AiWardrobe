from datetime import datetime, timedelta, timezone
from random import SystemRandom

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db
from app.email_service import send_verification_code
from app.models import EmailCode, User, utc_now
from app.schemas import EmailCodeAccepted, EmailCodeRequest, EmailCodeVerify, PasswordAuthRequest, TokenResponse
from app.security import create_access_token, hash_code, hash_password, verify_code, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
random = SystemRandom()


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(body: PasswordAuthRequest, db: Session = Depends(get_db)) -> TokenResponse:
    email = body.email.lower()
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Email already registered")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(access_token=create_access_token(user.id), user=user)


@router.post("/login", response_model=TokenResponse)
def login(body: PasswordAuthRequest, db: Session = Depends(get_db)) -> TokenResponse:
    email = body.email.lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=400, detail="Invalid email or password")
    return TokenResponse(access_token=create_access_token(user.id), user=user)


@router.post("/email-code/request", response_model=EmailCodeAccepted, status_code=202)
def request_email_code(
    body: EmailCodeRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> EmailCodeAccepted:
    code = f"{random.randint(0, 999999):06d}"
    email_code = EmailCode(
        email=body.email.lower(),
        code_hash=hash_code(code),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.email_code_minutes),
    )
    db.add(email_code)
    db.commit()
    send_verification_code(settings, body.email, code)
    expose_dev_code = settings.testing or (settings.environment != "production" and not settings.smtp_host)
    return EmailCodeAccepted(message="Verification code sent", dev_code=code if expose_dev_code else None)


@router.post("/email-code/verify", response_model=TokenResponse)
def verify_email_code(body: EmailCodeVerify, db: Session = Depends(get_db)) -> TokenResponse:
    email = body.email.lower()
    code_row = db.execute(
        select(EmailCode)
        .where(EmailCode.email == email, EmailCode.consumed_at.is_(None))
        .order_by(desc(EmailCode.created_at))
        .limit(1)
    ).scalar_one_or_none()

    now = utc_now()
    expires_at = code_row.expires_at if code_row is not None else None
    if expires_at is not None and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if code_row is None or expires_at is None or expires_at < now or not verify_code(body.code, code_row.code_hash):
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    code_row.consumed_at = now
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None:
        user = User(email=email)
        db.add(user)
        db.flush()
    db.commit()
    db.refresh(user)
    return TokenResponse(access_token=create_access_token(user.id), user=user)
