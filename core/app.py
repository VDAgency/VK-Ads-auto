"""Фабрика FastAPI-приложения — единая точка сборки headless-ядра.

Вся бизнес-логика живёт в `core`/`services`; каналы (бот, веб) — тонкие клиенты
поверх внутреннего API. Здесь только сборка приложения и подключение роутеров.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from core.api import health
from core.api.v1 import router as v1_router

# Каталог статики веба (лендинг + формы брифа). Корень репозитория / web / static.
_STATIC_DIR = Path(__file__).resolve().parent.parent / "web" / "static"


def create_app() -> FastAPI:
    """Собрать и вернуть экземпляр FastAPI-приложения."""
    app = FastAPI(title="VK-Ads-auto", version="0.0.0")
    app.include_router(health.router)
    app.include_router(v1_router.router)
    # Статику монтируем ПОСЛЕ API — /health и /api/v1 имеют приоритет.
    if _STATIC_DIR.is_dir():
        app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="web")
    return app


app = create_app()
