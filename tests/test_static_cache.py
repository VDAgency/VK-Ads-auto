"""Кэш-политика статики: HTML перепроверяется, хешированные ассеты кешируются.

HTML обязан оставаться `no-cache`: иначе браузер отдаёт старую форму брифа без
токена `?t=`, и инвайт не помечается received (исторический баг).

Ассеты под `/_next/static/` несут хеш содержимого в имени, поэтому их можно
кешировать надолго: изменился файл — изменилось имя. Прежняя сплошная политика
`no-cache` заставляла браузер перепроверять около двадцати файлов на каждый
заход.
"""

from __future__ import annotations

import pytest
from core.app import _STATIC_DIR, create_app
from fastapi.testclient import TestClient

from tests.test_web_static import stylesheet_hrefs

pytestmark = pytest.mark.skipif(
    not _STATIC_DIR.is_dir(),
    reason="нет собранного фронта: выполните `cd web && npm run build`",
)


def test_html_is_revalidated() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/brief-individual.html")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"


def test_hashed_asset_is_cached_immutably() -> None:
    with TestClient(create_app()) as client:
        hrefs = stylesheet_hrefs(client.get("/brief-individual.html").text)
        assert hrefs, "страница обязана подключать таблицу стилей"
        asset = next(href for href in hrefs if href.startswith("/_next/static/"))
        resp = client.get(asset)
    assert resp.status_code == 200
    cache_control = resp.headers.get("cache-control", "")
    assert "immutable" in cache_control
    assert "max-age=31536000" in cache_control


def test_extensionless_route_still_resolves_to_html() -> None:
    """Фолбэк на `.html` не должен пострадать от смены политики."""
    with TestClient(create_app()) as client:
        resp = client.get("/cabinet")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"
