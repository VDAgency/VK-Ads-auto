"""Хранилище токена сообщества (`db/community_tokens.py`): шифрование и замена.

Токен нужен только для проверки подключения Senler перед запуском
(`services/launch_service.py`) — сюда он приходит уже как значение, шифруется
здесь же (Fernet, `services.secret_box`, тем же ключом, что у `AdAccount`).

Клиенты в брифе почти всегда присылают короткий адрес сообщества, а не
числовой id, поэтому `find_decrypted_token` обязан находить токен по любому из
двух признаков — это и есть главный сценарий доработки (2026-08-24).
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
    CommunityTokenMatch,
    delete_community_token,
    find_decrypted_token,
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
SCREEN_NAME = "djbeauty"
COMMUNITY_NAME = "DJ BEAUTY"


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


async def _save(
    session: AsyncSession,
    account_id: int,
    token: str,
    *,
    community_id: str = COMMUNITY_ID,
    screen_name: str = SCREEN_NAME,
    community_name: str = COMMUNITY_NAME,
) -> CommunityTokenInfo:
    return await save_community_token(
        session,
        account_id,
        community_id,
        token,
        screen_name=screen_name,
        community_name=community_name,
        settings=_settings(),
    )


def test_saved_token_is_readable_decrypted() -> None:
    async def scenario(session: AsyncSession) -> str | None:
        await _save(session, 1, "secret-token-value")
        await session.commit()
        return await get_decrypted_token(session, 1, COMMUNITY_ID, settings=_settings())

    assert asyncio.run(_with_db(scenario)) == "secret-token-value"


def test_stored_row_never_holds_the_plaintext_token() -> None:
    """Проверяем сам столбец в БД — там обязан быть шифротекст, а не секрет как есть."""

    async def scenario(session: AsyncSession) -> str:
        await _save(session, 1, "super-secret-value")
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
        info = await _save(session, 1, "super-secret-value")
        await session.commit()
        return info

    info = asyncio.run(_with_db(scenario))
    assert not hasattr(info, "token")
    assert not hasattr(info, "token_encrypted")
    assert "super-secret-value" not in repr(info)
    assert info.community_id == COMMUNITY_ID
    assert info.screen_name == SCREEN_NAME
    assert info.community_name == COMMUNITY_NAME


def test_screen_name_is_stored_lowercased() -> None:
    """VK может отдать смешанный регистр — сравнение при поиске всегда без регистра."""

    async def scenario(session: AsyncSession) -> str:
        info = await _save(session, 1, "token", screen_name="DjBeauty")
        await session.commit()
        return info.screen_name

    assert asyncio.run(_with_db(scenario)) == "djbeauty"


def test_second_token_replaces_the_first() -> None:
    async def scenario(session: AsyncSession) -> str | None:
        await _save(session, 1, "old-token")
        await session.commit()
        await _save(session, 1, "new-token")
        await session.commit()
        return await get_decrypted_token(session, 1, COMMUNITY_ID, settings=_settings())

    assert asyncio.run(_with_db(scenario)) == "new-token"


def test_second_token_does_not_leave_two_active_rows() -> None:
    """Замена токена архивирует прежнюю строку, а не плодит второй активный дубль."""

    async def scenario(session: AsyncSession) -> int:
        await _save(session, 1, "old-token")
        await session.commit()
        await _save(session, 1, "new-token")
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
        await _save(session, 1, "token")
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
        await _save(session, 2, "tenant-two-token")
        await session.commit()
        return await get_decrypted_token(session, 1, COMMUNITY_ID, settings=_settings())

    assert asyncio.run(_with_db(scenario)) is None


# --- find_decrypted_token: сообщество из брифа почти всегда короткий адрес -------


def test_find_by_numeric_community_id() -> None:
    """`vk.com/club228817082` — числовой id есть, находим по нему."""

    async def scenario(session: AsyncSession) -> CommunityTokenMatch | None:
        await _save(session, 1, "community-token")
        await session.commit()
        return await find_decrypted_token(
            session, 1, community_id=COMMUNITY_ID, screen_name=None, settings=_settings()
        )

    match = asyncio.run(_with_db(scenario))
    assert match is not None
    assert match.token == "community-token"
    assert match.community_id == COMMUNITY_ID


def test_find_by_short_address_when_url_has_no_numeric_id() -> None:
    """Главный сценарий: `https://vk.ru/djbeauty` не содержит числового id —
    находим токен по короткому адресу, сохранённому при привязке."""

    async def scenario(session: AsyncSession) -> CommunityTokenMatch | None:
        await _save(session, 1, "community-token")
        await session.commit()
        return await find_decrypted_token(
            session, 1, community_id=None, screen_name="djbeauty", settings=_settings()
        )

    match = asyncio.run(_with_db(scenario))
    assert match is not None
    assert match.token == "community-token"
    # Даже найдя по короткому адресу, отдаём канонический числовой id — он
    # нужен `groups.getCallbackServers`.
    assert match.community_id == COMMUNITY_ID


def test_find_by_short_address_is_case_insensitive() -> None:
    async def scenario(session: AsyncSession) -> CommunityTokenMatch | None:
        await _save(session, 1, "community-token")
        await session.commit()
        return await find_decrypted_token(
            session, 1, community_id=None, screen_name="DjBeauty", settings=_settings()
        )

    match = asyncio.run(_with_db(scenario))
    assert match is not None
    assert match.token == "community-token"


def test_find_returns_none_when_neither_reference_matches() -> None:
    async def scenario(session: AsyncSession) -> CommunityTokenMatch | None:
        await _save(session, 1, "community-token")
        await session.commit()
        return await find_decrypted_token(
            session, 1, community_id="999", screen_name="other-address", settings=_settings()
        )

    assert asyncio.run(_with_db(scenario)) is None


def test_find_returns_none_when_both_references_are_empty() -> None:
    async def scenario(session: AsyncSession) -> CommunityTokenMatch | None:
        await _save(session, 1, "community-token")
        await session.commit()
        return await find_decrypted_token(
            session, 1, community_id=None, screen_name=None, settings=_settings()
        )

    assert asyncio.run(_with_db(scenario)) is None


def test_find_is_scoped_to_tenant() -> None:
    async def scenario(session: AsyncSession) -> CommunityTokenMatch | None:
        session.add(Account(id=2, name="tenant-two"))
        await session.commit()
        await _save(session, 2, "tenant-two-token")
        await session.commit()
        return await find_decrypted_token(
            session, 1, community_id=None, screen_name="djbeauty", settings=_settings()
        )

    assert asyncio.run(_with_db(scenario)) is None


def test_find_does_not_match_archived_token() -> None:
    async def scenario(session: AsyncSession) -> CommunityTokenMatch | None:
        await _save(session, 1, "old-token")
        await session.commit()
        removed = await delete_community_token(session, 1, COMMUNITY_ID)
        assert removed is True
        await session.commit()
        return await find_decrypted_token(
            session, 1, community_id=None, screen_name="djbeauty", settings=_settings()
        )

    assert asyncio.run(_with_db(scenario)) is None
