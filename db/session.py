"""Async-движок и фабрика сессий SQLAlchemy + FastAPI-зависимость.

Каналы/роутеры не создают движок сами — берут сессию через `get_session`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from functools import lru_cache

from config.settings import get_settings
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@lru_cache
def get_engine() -> AsyncEngine:
    """Создать (один раз) async-движок по строке подключения из настроек."""
    return create_async_engine(get_settings().database_url, pool_pre_ping=True)


@lru_cache
def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    """Фабрика async-сессий."""
    return async_sessionmaker(get_engine(), expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-зависимость: выдать сессию на время запроса."""
    async with get_sessionmaker()() as session:
        yield session
