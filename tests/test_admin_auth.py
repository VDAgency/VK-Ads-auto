"""Тесты авторизации админки (`services/admin_auth`)."""

from __future__ import annotations

from services.admin_auth import (
    generate_admin_link,
    generate_admin_session,
    verify_admin_link,
    verify_admin_session,
)
from services.session_token import generate_session

SECRET = "admin-secret"


def test_link_roundtrip() -> None:
    token = generate_admin_link(555, SECRET)
    assert verify_admin_link(token, SECRET) == 555


def test_session_roundtrip() -> None:
    token = generate_admin_session(555, SECRET)
    assert verify_admin_session(token, SECRET) == 555


def test_link_not_accepted_as_session_and_vice_versa() -> None:
    # Метки purpose разделяют magic-link и сессию.
    link = generate_admin_link(555, SECRET)
    sess = generate_admin_session(555, SECRET)
    assert verify_admin_session(link, SECRET) is None
    assert verify_admin_link(sess, SECRET) is None


def test_client_session_not_accepted_as_admin() -> None:
    # Клиентская сессия (метка sess) не проходит как админская.
    client_sess = generate_session(555, SECRET)
    assert verify_admin_session(client_sess, SECRET) is None
    assert verify_admin_link(client_sess, SECRET) is None


def test_wrong_secret_and_tamper_rejected() -> None:
    token = generate_admin_link(555, SECRET)
    assert verify_admin_link(token, "other") is None
    assert verify_admin_link(token[:-2] + "zz", SECRET) is None


def test_expired_rejected() -> None:
    assert verify_admin_link(generate_admin_link(555, SECRET, ttl_seconds=-1), SECRET) is None
    assert verify_admin_session(generate_admin_session(555, SECRET, ttl_seconds=-1), SECRET) is None


def test_garbage_rejected() -> None:
    assert verify_admin_link("not-base64!!!", SECRET) is None
    assert verify_admin_session("", SECRET) is None
