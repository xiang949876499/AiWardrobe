from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    garments: Mapped[list["Garment"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    outfits: Mapped[list["Outfit"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    uploads: Mapped[list["UploadSession"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class EmailCode(Base):
    __tablename__ = "email_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class Garment(Base):
    __tablename__ = "garments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    source_upload_id: Mapped[str | None] = mapped_column(ForeignKey("upload_sessions.id"), nullable=True)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    image_key: Mapped[str] = mapped_column(String(512), nullable=False)
    thumbnail_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    colors: Mapped[list[str]] = mapped_column(JSON, default=list)
    style: Mapped[str] = mapped_column(String(120), default="")
    material: Mapped[str] = mapped_column(String(120), default="")
    season: Mapped[list[str]] = mapped_column(JSON, default=list)
    fit: Mapped[str] = mapped_column(String(120), default="")
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    crop_box: Mapped[dict[str, int] | None] = mapped_column(JSON, nullable=True)
    ai_result: Mapped[dict[str, object]] = mapped_column(JSON, default=dict)
    ai_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), index=True, default="uploaded")
    review_status: Mapped[str] = mapped_column(String(32), index=True, default="confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user: Mapped[User] = relationship(back_populates="garments")
    upload_session: Mapped["UploadSession | None"] = relationship(back_populates="garments")


class UploadSession(Base):
    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    original_image_url: Mapped[str] = mapped_column(Text, nullable=False)
    original_image_key: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, default="uploaded")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    user: Mapped[User] = relationship(back_populates="uploads")
    garments: Mapped[list[Garment]] = relationship(back_populates="upload_session", cascade="all, delete-orphan")


class Outfit(Base):
    __tablename__ = "outfits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(160), default="")
    occasion: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    season: Mapped[str] = mapped_column(String(64), default="")
    temperature: Mapped[int | None] = mapped_column(Integer, nullable=True)
    items: Mapped[list[dict[str, object]]] = mapped_column(JSON, default=list)
    explanation: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(32), index=True, default="ai")
    is_favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    is_fixed: Mapped[bool] = mapped_column(Boolean, default=False)
    weather_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    user: Mapped[User] = relationship(back_populates="outfits")


class WeatherCache(Base):
    __tablename__ = "weather_cache"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True, nullable=False)
    date: Mapped[str] = mapped_column(String(10), index=True, nullable=False)
    lat_key: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    lon_key: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    city: Mapped[str] = mapped_column(String(120), default="")
    condition: Mapped[str] = mapped_column(String(120), default="")
    temperature: Mapped[int] = mapped_column(Integer, default=22)
    feels_like: Mapped[int] = mapped_column(Integer, default=22)
    precipitation: Mapped[float] = mapped_column(Float, default=0.0)
    wind_speed: Mapped[float] = mapped_column(Float, default=0.0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
