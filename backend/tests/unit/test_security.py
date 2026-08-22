"""Unit tests for password hashing and JWT issue/verify (no DB, no network)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from app.core.config import get_settings
from app.core.constants import TokenType
from app.core.security import (
    TokenError,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    hash_refresh_token,
    password_needs_rehash,
    verify_password,
)


def test_password_hash_roundtrip() -> None:
    hashed = hash_password("s3cret-password")
    assert hashed != "s3cret-password"
    assert verify_password("s3cret-password", hashed) is True
    assert verify_password("wrong-password", hashed) is False


def test_password_hash_is_argon2() -> None:
    assert hash_password("abc12345").startswith("$argon2")


def test_verify_against_malformed_hash_is_false() -> None:
    assert verify_password("anything", "not-a-valid-hash") is False


def test_password_needs_rehash_false_for_fresh_hash() -> None:
    assert password_needs_rehash(hash_password("abc12345")) is False


def test_access_token_roundtrip() -> None:
    issued = create_access_token("user-123", org_id="org-1", role="admin")
    decoded = decode_token(issued.token, expected_type=TokenType.ACCESS)
    assert decoded.subject == "user-123"
    assert decoded.org_id == "org-1"
    assert decoded.role == "admin"
    assert decoded.token_type == TokenType.ACCESS
    assert decoded.jti == issued.jti


def test_refresh_token_roundtrip() -> None:
    issued = create_refresh_token("u", org_id="o", role="end_user")
    decoded = decode_token(issued.token, expected_type=TokenType.REFRESH)
    assert decoded.token_type == TokenType.REFRESH


def test_wrong_token_type_rejected() -> None:
    access = create_access_token("u")
    with pytest.raises(TokenError):
        decode_token(access.token, expected_type=TokenType.REFRESH)


def test_tampered_token_rejected() -> None:
    access = create_access_token("u")
    with pytest.raises(TokenError):
        decode_token(access.token + "tampered", expected_type=TokenType.ACCESS)


def test_expired_token_rejected() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": "u",
        "type": "access",
        "jti": "j",
        "iat": now - timedelta(hours=2),
        "nbf": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    token = jwt.encode(
        payload, settings.SECRET_KEY.get_secret_value(), algorithm=settings.JWT_ALGORITHM
    )
    with pytest.raises(TokenError):
        decode_token(token, expected_type=TokenType.ACCESS)


def test_wrong_signing_key_rejected() -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": "u",
        "type": "access",
        "jti": "j",
        "iat": now,
        "nbf": now,
        "exp": now + timedelta(minutes=5),
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
    }
    token = jwt.encode(payload, "a-different-secret-key", algorithm="HS256")
    with pytest.raises(TokenError):
        decode_token(token, expected_type=TokenType.ACCESS)


def test_refresh_hash_is_deterministic_and_distinct() -> None:
    assert hash_refresh_token("abc") == hash_refresh_token("abc")
    assert hash_refresh_token("abc") != hash_refresh_token("abd")
