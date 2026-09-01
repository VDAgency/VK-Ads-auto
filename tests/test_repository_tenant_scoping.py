"""Скоуп по `account_id` для функций репозитория инвайтов (CLAUDE.md §1.3).

Пять функций (`mark_invite_sent`, `mark_invite_failed`, `mark_invite_superseded`,
`mark_invite_received_if_sent`, `find_brief_invite_by_token`) раньше ходили в
`brief_invite` только по `invite_id`/`token` — без проверки тенанта. При одном
тенанте это не эксплуатировалось, но нарушало инвариант мульти-тенантности и
рано или поздно позволило бы одному оператору задеть чужой инвайт по id/токену.

Два вида проверки:
1. Поведенческая — операция с чужим `account_id` не находит объект и не меняет
   строку (образец: `tests/test_campaign_stop_endpoint.py::test_stop_does_not_cross_tenants`).
2. Сторож — регуляркой по исходнику `db/repositories.py` не даёт откатить скоуп.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from db.base import Base
from db.models import Account, BriefInvite, Operator
from db.repositories import (
    create_brief_invite,
    find_brief_invite_by_token,
    mark_invite_failed,
    mark_invite_received_if_sent,
    mark_invite_sent,
    mark_invite_superseded,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_OWNER_ACCOUNT_ID = 1
_OTHER_ACCOUNT_ID = 2
_OPERATOR_ID = 10


async def _with_two_tenants(scenario: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Поднять in-memory БД с двумя тенантами (1 — владелец инвайта, 2 — чужой)."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add(Account(id=_OWNER_ACCOUNT_ID, name="owner"))
        session.add(Account(id=_OTHER_ACCOUNT_ID, name="intruder"))
        session.add(Operator(id=_OPERATOR_ID, account_id=_OWNER_ACCOUNT_ID, telegram_id=555))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


async def _create_owned_invite(session: AsyncSession, token: str = "tok-scope") -> BriefInvite:
    return await create_brief_invite(
        session,
        account_id=_OWNER_ACCOUNT_ID,
        operator_id=_OPERATOR_ID,
        token=token,
        variant="individual",
        contact_type="email",
        contact_value="a@b.c",
        channel="email",
    )


def test_mark_invite_sent_does_not_cross_tenants() -> None:
    """Инвайт чужого тенанта пометить нельзя: статус остаётся прежним."""

    async def scenario(session: AsyncSession) -> tuple[str, bool]:
        invite = await _create_owned_invite(session)
        await mark_invite_sent(session, _OTHER_ACCOUNT_ID, invite.id, contact_name="Чужой")
        await session.refresh(invite)
        return invite.status, invite.delivered_at is not None

    status, has_delivered_at = asyncio.run(_with_two_tenants(scenario))
    assert status == "pending"
    assert not has_delivered_at


def test_mark_invite_failed_does_not_cross_tenants() -> None:
    """Пометить чужой инвайт failed нельзя: статус и error остаются прежними."""

    async def scenario(session: AsyncSession) -> tuple[str, str | None]:
        invite = await _create_owned_invite(session)
        await mark_invite_failed(session, _OTHER_ACCOUNT_ID, invite.id, "intruder_error")
        await session.refresh(invite)
        return invite.status, invite.error

    status, error = asyncio.run(_with_two_tenants(scenario))
    assert status == "pending"
    assert error is None


def test_mark_invite_superseded_does_not_cross_tenants() -> None:
    """Пометить чужой failed-инвайт superseded нельзя: статус остаётся failed."""

    async def scenario(session: AsyncSession) -> str:
        invite = await _create_owned_invite(session)
        await mark_invite_failed(session, _OWNER_ACCOUNT_ID, invite.id, "own_error")
        await mark_invite_superseded(session, _OTHER_ACCOUNT_ID, invite.id)
        await session.refresh(invite)
        return invite.status

    assert asyncio.run(_with_two_tenants(scenario)) == "failed"


def test_mark_invite_received_if_sent_does_not_cross_tenants() -> None:
    """Атомарный переход sent→received с чужим account_id не срабатывает."""

    async def scenario(session: AsyncSession) -> tuple[bool, str]:
        invite = await _create_owned_invite(session)
        await mark_invite_sent(session, _OWNER_ACCOUNT_ID, invite.id)
        ok = await mark_invite_received_if_sent(
            session, _OTHER_ACCOUNT_ID, invite.id, contact_name="Чужой"
        )
        await session.refresh(invite)
        return ok, invite.status

    ok, status = asyncio.run(_with_two_tenants(scenario))
    assert ok is False
    assert status == "sent"


def test_find_brief_invite_by_token_does_not_cross_tenants() -> None:
    """Токен глобально уникален, но чужой тенант по нему инвайт не находит."""

    async def scenario(session: AsyncSession) -> BriefInvite | None:
        await _create_owned_invite(session, token="tok-owner")
        return await find_brief_invite_by_token(session, _OTHER_ACCOUNT_ID, "tok-owner")

    assert asyncio.run(_with_two_tenants(scenario)) is None


def test_find_brief_invite_by_token_finds_it_for_owner() -> None:
    """Контрольная проверка: свой тенант тот же инвайт по токену находит."""

    async def scenario(session: AsyncSession) -> BriefInvite | None:
        await _create_owned_invite(session, token="tok-owner")
        return await find_brief_invite_by_token(session, _OWNER_ACCOUNT_ID, "tok-owner")

    found = asyncio.run(_with_two_tenants(scenario))
    assert found is not None
    assert found.token == "tok-owner"


def test_public_repository_functions_are_tenant_scoped() -> None:
    """Публичная функция репозитория обязана скоупиться по account_id.

    Иначе запрос молча выйдет за границу тенанта (CLAUDE.md §1.3). Исключения
    перечислены поимённо и требуют письменного обоснования прямо здесь.

    Регулярка нарочно берёт только `async def`: на момент написания теста в
    `db/repositories.py` НЕТ ни одной публичной функции-репозитория, объявленной
    обычным `def` (проверено `grep -n "\\bdef \\b" db/repositories.py` — все 55
    определений начинаются с `async def`). Если появится синхронная публичная
    функция, сторож её не увидит — расширить регулярку на `(?:async )?def`
    вместе с добавлением первой такой функции, не заранее.
    """
    import inspect
    import re
    from pathlib import Path

    # `set_operator_password_hash` принимает уже найденный под тенантом объект
    # Operator, а не идентификатор, — скоупить нечего.
    allowed = {"set_operator_password_hash"}

    source = Path("db/repositories.py").read_text(encoding="utf-8")
    unscoped = [
        name
        for name, args in re.findall(r"^async def (\w+)\(([^)]*)\)", source, re.M | re.S)
        if not name.startswith("_") and "account_id" not in args and name not in allowed
    ]
    assert not unscoped, (
        f"Функции без account_id: {unscoped}. Добавьте скоуп по тенанту или впишите "
        "в список исключений с обоснованием."
    )
    # Инспекцией дублируем: ни один экспортируемый callable модуля не должен
    # выпасть из regex-проверки (расхождение сигнализировало бы про дыру в самой
    # регулярке, а не только про отсутствие account_id).
    import db.repositories as repo_module

    exported = [
        name
        for name, obj in vars(repo_module).items()
        if inspect.isfunction(obj)
        and obj.__module__ == repo_module.__name__
        and not name.startswith("_")
    ]
    names_seen_by_regex = set(re.findall(r"^async def (\w+)\(", source, re.M))
    missing_from_regex = set(exported) - names_seen_by_regex
    assert not missing_from_regex, (
        f"Функции не пойманы регуляркой сторожа (возможно, объявлены не как "
        f"`async def`): {missing_from_regex}. Расширить регулярку теста."
    )


def test_mark_invite_sent_still_works_for_owner() -> None:
    """Контрольная проверка: свой тенант по-прежнему может пометить инвайт sent."""

    async def scenario(session: AsyncSession) -> str:
        invite = await _create_owned_invite(session)
        await mark_invite_sent(session, _OWNER_ACCOUNT_ID, invite.id)
        await session.refresh(invite)
        return invite.status

    assert asyncio.run(_with_two_tenants(scenario)) == "sent"
