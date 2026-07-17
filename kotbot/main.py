"""FastAPI-приложение kotbot-сервиса (spec §3, §4).

Собирает `KotbotAutomation` (синглтон на процесс) из настроек и отдаёт её
роутерам через dependency. В каркасе бэкенд — `NullBackend`; реальный
Playwright-бэкенд подключится в K-PR3. Порт 8002 наружу не публикуется
(см. docker-compose).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from kotbot.api import actions, auth, health
from kotbot.backend import NullBackend
from kotbot.config import get_settings
from kotbot.service import KotbotAutomation
from kotbot.store import CredentialStore, StateStore


def build_automation() -> KotbotAutomation:
    """Собрать `KotbotAutomation` из настроек сервиса."""
    settings = get_settings()
    return KotbotAutomation(
        credentials=CredentialStore(settings.secret_key, settings.secrets_dir),
        states=StateStore(settings.secret_key, settings.secrets_dir),
        backend=NullBackend(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Создать автоматизацию на старте, положить в state для роутеров.

    Если она уже задана (тест инъектировал свою) — не перезаписываем.
    """
    if getattr(app.state, "automation", None) is None:
        app.state.automation = build_automation()
    yield


def create_app() -> FastAPI:
    """Фабрика приложения (упрощает тестирование с подменённой автоматизацией)."""
    app = FastAPI(title="VK-Ads-auto kotbot", lifespan=lifespan)
    app.include_router(auth.router)
    app.include_router(actions.router)
    app.include_router(health.router)
    return app


app = create_app()
