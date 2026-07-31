"""FastAPI-приложение userbot-сервиса (spec §6).

Собирает единый `UserbotClient` (синглтон на процесс) из настроек и отдаёт его
роутерам через dependency. Порт наружу не публикуется (см. docker-compose).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from userbot.api import auth, health, send
from userbot.config import get_settings
from userbot.endpoint_cache import EndpointCache
from userbot.endpoints import (
    EndpointResolver,
    parse_dc_order,
    parse_endpoints,
    parse_ports,
    parse_transport,
    parse_transports,
)
from userbot.proxy import parse_proxy
from userbot.session import SessionStore
from userbot.telethon_client import UserbotClient, default_client_factory

logger = logging.getLogger(__name__)


def build_client() -> UserbotClient:
    """Собрать `UserbotClient` из настроек сервиса."""
    settings = get_settings()
    cache = EndpointCache(settings.sessions_dir)
    proxy = parse_proxy(settings.proxy.get_secret_value())
    if proxy is not None:
        logger.info("прокси настроен: %s", proxy.describe())
    # MTProxy работает только со своим транспортом — он перекрывает настройку.
    transport = parse_transport(settings.transport)
    if proxy is not None and proxy.transport is not None:
        transport = proxy.transport
    resolver = EndpointResolver(
        pinned=parse_endpoints(settings.dc_endpoints, transport),
        ports=parse_ports(settings.dc_ports),
        transport=transport,
        transport_fallbacks=parse_transports(settings.transport_fallbacks),
        auth_dc_order=parse_dc_order(settings.auth_dc_order),
        proxy_configured=proxy is not None,
        cache_lookup=cache.get,
    )
    factory = default_client_factory(settings, resolver, proxy)
    store = SessionStore(
        secret_key=settings.secret_key.get_secret_value(),
        sessions_dir=settings.sessions_dir,
    )
    return UserbotClient(
        factory=factory,
        store=store,
        resolver=resolver,
        cache=cache,
        rounds=settings.connect_rounds,
        budget=settings.connect_budget,
    )


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
