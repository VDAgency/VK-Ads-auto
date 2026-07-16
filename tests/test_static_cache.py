"""Статика отдаётся с Cache-Control: no-cache — браузер всегда берёт свежий JS/HTML.

Без этого браузер кешировал форму брифа и слал POST без токена `?t=`, из-за чего
инвайт не помечался received (см. фикс формы + этот заголовок).
"""

from __future__ import annotations

from core.app import create_app
from fastapi.testclient import TestClient


def test_static_js_has_no_cache_header() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/app.js")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"


def test_static_html_has_no_cache_header() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/brief-individual.html")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"
