"""Текстовая статика отдаётся сжатой.

Фронт Блока 2 — около 660 КБ JS и 33 КБ CSS. `StaticFiles` не сжимает ничего
сам, и замер прод-сборки показал 421 КБ лишнего трафика на один визит лендинга
(736.7 → 208.0 КБ по всей сборке, экономия 72%). Аудитория часто на мобильном
интернете, поэтому проверяем не «middleware подключён», а факт сжатого ответа.
"""

from __future__ import annotations

import pytest
from core.app import _STATIC_DIR, create_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif(
    not _STATIC_DIR.is_dir(),
    reason="нет собранного фронта: выполните `cd web && npm run build`",
)


def test_html_is_gzipped() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/brief-individual.html", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") == "gzip"


def test_plain_client_still_gets_uncompressed_body() -> None:
    """Клиент без поддержки gzip обязан получить рабочий ответ."""
    with TestClient(create_app()) as client:
        resp = client.get("/brief-individual.html", headers={"Accept-Encoding": "identity"})
    assert resp.status_code == 200
    assert resp.headers.get("content-encoding") != "gzip"
    assert "<html" in resp.text.lower()
