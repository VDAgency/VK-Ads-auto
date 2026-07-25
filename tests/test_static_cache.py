"""Статика отдаётся с Cache-Control: no-cache — браузер всегда берёт свежий HTML/CSS.

Без этого браузер кешировал форму брифа и слал POST без токена `?t=`, из-за чего
инвайт не помечался received (см. фикс формы + этот заголовок).

Раньше здесь проверялся `/app.js`. После переноса форм на Next этого файла нет:
его логика живёт в `web/components/BriefForm.tsx`, а скрипты Next отдаются как
хешированные чанки под `/_next/`. Свойство проверяется на тех ассетах, которые
остались стабильными по адресу.
"""

from __future__ import annotations

from core.app import create_app
from fastapi.testclient import TestClient


def test_static_css_has_no_cache_header() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/styles.css")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"


def test_static_html_has_no_cache_header() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/brief-individual.html")
    assert resp.status_code == 200
    assert resp.headers.get("cache-control") == "no-cache"
