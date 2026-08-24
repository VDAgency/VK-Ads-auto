"""Хранилище токена сообщества (`db/community_tokens.py`): шифрование и замена.

Токен нужен только для проверки подключения Senler перед запуском
(`services/launch_service.py`) — сюда он приходит уже как значение, шифруется
здесь же (Fernet, `services.secret_box`, тем же ключом, что у `AdAccount`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from config.settings import Settings
from cryptography.fernet import Fernet
from db.base import Base
from db.community_tokens import (
    CommunityTokenInfo,
    delete_community_token,
    get_decrypted_token,
    save_community_token,
)
from db.models import Account, CommunityToken
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_KEY = Fernet.generate_key().decode()
COMMUNITY_ID = "228817082"


def _settings(**over: object) -> Settings:
    base: dict[str, object] = {"_env_file": None, "vk_ads_secret_key": SecretStr(_KEY)}
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


async def _with_db(scenario: Callable[[AsyncSession], Awaitable[T]]) -> T:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(Account(id=1, name="tenant-one"))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def test_saved_token_is_readable_decrypted() -> None:
    async def scenario(session: AsyncSession) -> str | None:
        await save_community_token(
            session, 1, COMMUNITY_ID, "secret-token-value", settings=_settings()
        )
        await session.commit()
        return await get_decrypted_token(session, 1, COMMUNITY_ID, settings=_settings())

    assert asyncio.run(_with_db(scenario)) == "secret-token-value"


def test_stored_row_never_holds_the_plaintext_token() -> None:
    """Проверяем сам столбец в БД — там обязан быть шифротекст, а не секрет как есть."""

    async def scenario(session: AsyncSession) -> str:
        await save_community_token(
            session, 1, COMMUNITY_ID, "super-secret-value", settings=_settings()
        )
        await session.commit()
        row = (
            await session.execute(
                select(CommunityToken).where(CommunityToken.community_id == COMMUNITY_ID)
            )
        ).scalar_one()
        return row.token_encrypted

    stored = asyncio.run(_with_db(scenario))
    assert "super-secret-value" not in stored


def test_saved_token_info_never_carries_the_secret() -> None:
    """Возвращаемый объект — только метаданные; ни plaintext, ни шифротекст туда не попадают."""

    async def scenario(session: AsyncSession) -> CommunityTokenInfo:
        info = await save_community_token(
            session, 1, COMMUNITY_ID, "super-secret-value", settings=_settings()
        )
        await session.commit()
        return info

    info = asyncio.run(_with_db(scenario))
    assert not hasattr(info, "token")
    assert not hasattr(info, "token_encrypted")
    assert "super-secret-value" not in repr(info)
    assert info.community_id == COMMUNITY_ID


def test_second_token_replaces_the_first() -> None:
    async def scenario(session: AsyncSession) -> str | None:
        await save_community_token(session, 1, COMMUNITY_ID, "old-token", settings=_settings())
        await session.commit()
        await save_community_token(session, 1, COMMUNITY_ID, "new-token", settings=_settings())
        await session.commit()
        return await get_decrypted_token(session, 1, COMMUNITY_ID, settings=_settings())

    assert asyncio.run(_with_db(scenario)) == "new-token"


def test_second_token_does_not_leave_two_active_rows() -> None:
    """Замена токена архивирует прежнюю строку, а не плодит второй активный дубль."""

    async def scenario(session: AsyncSession) -> int:
        await save_community_token(session, 1, COMMUNITY_ID, "old-token", settings=_settings())
        await session.commit()
        await save_community_token(session, 1, COMMUNITY_ID, "new-token", settings=_settings())
        await session.commit()
        rows = (
            (
                await session.execute(
                    select(CommunityToken).where(
                        CommunityToken.community_id == COMMUNITY_ID,
                        CommunityToken.status == "active",
                    )
                )
            )
            .scalars()
            .all()
        )
        return len(rows)

    assert asyncio.run(_with_db(scenario)) == 1


def test_missing_token_returns_none() -> None:
    async def scenario(session: AsyncSession) -> str | None:
        return await get_decrypted_token(session, 1, "no-such-community", settings=_settings())

    assert asyncio.run(_with_db(scenario)) is None


def test_deleted_token_is_no_longer_returned() -> None:
    async def scenario(session: AsyncSession) -> str | None:
        await save_community_token(session, 1, COMMUNITY_ID, "token", settings=_settings())
        await session.commit()
        removed = await delete_community_token(session, 1, COMMUNITY_ID)
        await session.commit()
        assert removed is True
        return await get_decrypted_token(session, 1, COMMUNITY_ID, settings=_settings())

    assert asyncio.run(_with_db(scenario)) is None


def test_deleting_unknown_token_reports_nothing_removed() -> None:
    async def scenario(session: AsyncSession) -> bool:
        return await delete_community_token(session, 1, "no-such-community")

    assert asyncio.run(_with_db(scenario)) is False


def test_tokens_are_scoped_to_tenant() -> None:
    """Токен, сохранённый для другого account_id, не виден этому тенанту."""

    async def scenario(session: AsyncSession) -> str | None:
        session.add(Account(id=2, name="tenant-two"))
        await session.commit()
        await save_community_token(
            session, 2, COMMUNITY_ID, "tenant-two-token", settings=_settings()
        )
        await session.commit()
        return await get_decrypted_token(session, 1, COMMUNITY_ID, settings=_settings())

    assert asyncio.run(_with_db(scenario)) is None
