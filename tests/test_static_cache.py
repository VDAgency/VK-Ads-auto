"""Статика отдаётся с Cache-Control: no-cache — браузер всегда берёт свежий HTML.

Без этого браузер кешировал форму брифа и слал POST без токена `?t=`, из-за чего
инвайт не помечался received (см. фикс формы + этот заголовок).

Раньше здесь проверялся `/app.js`. После переноса Блока 2 на Next этого файла нет:
логика живёт в `web/components/BriefForm.tsx`, а стили и скрипты собираются в
хешированные чанки под `/_next/`. Заголовок ставится на всё, что отдаёт ядро, —
проверяем и страницу, и реально подключённый ею ассет.
"""

from __future__ import annotations

from core.app import create_app
from fastapi.testclient import TestClient

from tests.test_web_static import stylesheet_hrefs


def test_static_html_has_no_cache_header() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/brief-individual.html")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"


def test_bundled_css_has_no_cache_header() -> None:
    with TestClient(create_app()) as client:
        hrefs = stylesheet_hrefs(client.get("/brief-individual.html").text)
        assert hrefs, "страница обязана подключать таблицу стилей"
        resp = client.get(hrefs[0])
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"
