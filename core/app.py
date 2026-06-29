"""Фабрика FastAPI-приложения — единая точка сборки headless-ядра.

Вся бизнес-логика живёт в `core`/`services`; каналы (бот, веб) — тонкие клиенты
поверх внутреннего API. Здесь только сборка приложения и подключение роутеров.
"""

from __future__ import annotations

from fastapi import FastAPI

from core.api import health
from core.api.v1 import router as v1_router


def create_app() -> FastAPI:
    """Собрать и вернуть экземпляр FastAPI-приложения."""
    app = FastAPI(title="VK-Ads-auto", version="0.0.0")
    app.include_router(health.router)
    app.include_router(v1_router.router)
    return app


app = create_app()
