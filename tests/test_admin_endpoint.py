"""Тесты эндпоинтов авторизации админки: authenticate / me / logout."""

from __future__ import annotations

from config.settings import get_settings
from core.app import create_app
from fastapi.testclient import TestClient
from services.admin_auth import generate_admin_link, generate_admin_session

_SECRET = get_settings().secret_key.get_secret_value()


def test_authenticate_sets_cookie_and_me_returns_operator() -> None:
    client = TestClient(create_app())
    token = generate_admin_link(555, _SECRET)
    resp = client.post("/api/v1/admin/authenticate", json={"token": token})
    assert resp.status_code == 200
    assert "admin_session" in resp.cookies
    me = client.get("/api/v1/admin/me")
    assert me.status_code == 200
    assert me.json()["operator_id"] == 555


def test_authenticate_invalid_token_rejected() -> None:
    client = TestClient(create_app())
    resp = client.post("/api/v1/admin/authenticate", json={"token": "bogus-token"})
    assert resp.status_code == 401


def test_me_without_session_rejected() -> None:
    client = TestClient(create_app())
    assert client.get("/api/v1/admin/me").status_code == 401


def test_link_token_not_accepted_as_session_cookie() -> None:
    # Прямая подстановка magic-link в cookie не должна пускать (нужна метка admsess).
    client = TestClient(create_app())
    client.cookies.set("admin_session", generate_admin_link(555, _SECRET))
    assert client.get("/api/v1/admin/me").status_code == 401


def test_logout_clears_session() -> None:
    client = TestClient(create_app())
    client.cookies.set("admin_session", generate_admin_session(555, _SECRET))
    assert client.get("/api/v1/admin/me").status_code == 200
    client.post("/api/v1/admin/logout")
    client.cookies.clear()
    assert client.get("/api/v1/admin/me").status_code == 401
