from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass


class InvalidAssetToken(ValueError):
    pass


@dataclass(frozen=True)
class AssetTokenClaims:
    asset_id: str
    user_id: str
    variant: str
    expires_at: int


def create_asset_token(
    secret: str,
    asset_id: str,
    user_id: str,
    variant: str,
    *,
    now: int | None = None,
    ttl_seconds: int = 300,
) -> tuple[str, int]:
    expires_at = (int(time.time()) if now is None else now) + ttl_seconds
    payload = f"v1|{asset_id}|{user_id}|{variant}|{expires_at}".encode()
    encoded = _encode(payload)
    signature = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
    return f"{encoded}.{_encode(signature)}", expires_at


def asset_content_url(secret: str, asset_id: str, user_id: str, variant: str = "thumbnail") -> str:
    token, _ = create_asset_token(secret, asset_id, user_id, variant)
    return f"/v2/assets/{asset_id}/content?variant={variant}&asset_token={token}"


def verify_asset_token(
    secret: str,
    token: str,
    *,
    asset_id: str,
    variant: str,
    now: int | None = None,
) -> AssetTokenClaims:
    try:
        encoded, signature = token.split(".", 1)
        expected = hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(signature)):
            raise InvalidAssetToken("invalid_asset_token")
        version, token_asset_id, user_id, token_variant, expiration = _decode(encoded).decode().split("|", 4)
        expires_at = int(expiration)
    except (ValueError, UnicodeDecodeError) as exc:
        if isinstance(exc, InvalidAssetToken):
            raise
        raise InvalidAssetToken("invalid_asset_token") from exc
    current_time = int(time.time()) if now is None else now
    if (
        version != "v1"
        or token_asset_id != asset_id
        or token_variant != variant
        or expires_at <= current_time
        or variant not in {"original", "thumbnail"}
    ):
        raise InvalidAssetToken("invalid_asset_token")
    return AssetTokenClaims(token_asset_id, user_id, token_variant, expires_at)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
