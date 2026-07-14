"""FastAPI-приложение userbot-сервиса (spec §6).

Собирает единый `UserbotClient` (синглтон на процесс) из настроек и отдаёт его
роутерам через dependency. Порт наружу не публикуется (см. docker-compose).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from userbot.api import auth, health, send
from userbot.config import get_settings
from userbot.session import SessionStore
from userbot.telethon_client import UserbotClient, default_client_factory


def build_client() -> UserbotClient:
    """Собрать `UserbotClient` из настроек сервиса."""
    settings = get_settings()
    factory = default_client_factory(settings.api_id, settings.api_hash.get_secret_value())
    store = SessionStore(
        secret_key=settings.secret_key.get_secret_value(),
        path=settings.session_path,
    )
    return UserbotClient(factory=factory, store=store)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Создать клиент на старте, положить в state для роутеров.

    Если клиент уже задан (тест инъектировал свой) — не перезаписываем.
    """
    if getattr(app.state, "client", None) is None:
        app.state.client = build_client()
    yield


def create_app() -> FastAPI:
    """Фабрика приложения (упрощает тестирование с подменённым клиентом)."""
    app = FastAPI(title="VK-Ads-auto userbot", lifespan=lifespan)
    app.include_router(auth.router)
    app.include_router(send.router)
    app.include_router(health.router)
    return app


app = create_app()
