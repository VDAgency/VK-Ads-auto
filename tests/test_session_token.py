"""Тесты session-токена кабинета (`services/session_token`)."""

from __future__ import annotations

from services.auth_magiclink import generate_token
from services.session_token import generate_session, verify_session

SECRET = "test-secret"


def test_roundtrip() -> None:
    token = generate_session(42, SECRET)
    assert verify_session(token, SECRET) == 42


def test_wrong_secret_rejected() -> None:
    token = generate_session(42, SECRET)
    assert verify_session(token, "other-secret") is None


def test_tampered_rejected() -> None:
    token = generate_session(42, SECRET)
    tampered = token[:-2] + ("aa" if not token.endswith("aa") else "bb")
    assert verify_session(tampered, SECRET) is None


def test_expired_rejected() -> None:
    token = generate_session(42, SECRET, ttl_seconds=-1)
    assert verify_session(token, SECRET) is None


def test_magic_link_not_accepted_as_session() -> None:
    # Токен magic-link (без метки sess) не должен проходить как сессия.
    magic = generate_token(42, SECRET)
    assert verify_session(magic, SECRET) is None


def test_garbage_rejected() -> None:
    assert verify_session("not-base64!!!", SECRET) is None
    assert verify_session("", SECRET) is None
