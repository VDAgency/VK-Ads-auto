import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import pytest
from db.base import Base
from db.models import Account
from services.brief_parser import BriefValidationError, BriefVariant
from services.briefs import intake_brief
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

VALID_INDIVIDUAL = {
    "full_name": "Иван Иванов",
    "object_url": "https://vk.com/ivan",
    "audience_description": "молодёжь 18-25",
    "geo": "Москва",
    "budget": "30000",
    "term": "месяц",
    "target_type": "личная страница",
    "email": "ivan@example.com",
    "phone": "+79990000001",
    "tax_id": "770700000000",
}

VALID_COMMUNITY = {
    "full_name": "Анна Петрова",
    "object_url": "https://vk.com/romashka",
    "audience_description": "женщины 25-45",
    "geo": "вся Россия",
    "budget": "50000",
    "term": "месяц",
    "niche": "доставка цветов",
    "org_type": "ИП",
    "product_description": "доставка букетов за 2 часа",
    "email": "anna@example.com",
    "phone": "+79990000002",
    "tax_id": "770700000000",
}


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
        session.add(Account(id=1, name="default"))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def test_intake_creates_client_and_brief() -> None:
    async def scenario(session: AsyncSession) -> tuple[int, int | None, str, str]:
        brief = await intake_brief(session, 1, BriefVariant.INDIVIDUAL, VALID_INDIVIDUAL)
        return brief.id, brief.client_id, brief.variant, brief.status

    brief_id, client_id, variant, status = asyncio.run(_with_db(scenario))
    assert brief_id >= 1
    assert client_id is not None
    assert variant == "individual"
    assert status == "received"


def test_intake_reuses_client_by_email() -> None:
    async def scenario(session: AsyncSession) -> tuple[int | None, int | None]:
        first = await intake_brief(session, 1, BriefVariant.INDIVIDUAL, VALID_INDIVIDUAL)
        second_payload = {**VALID_INDIVIDUAL, "full_name": "Иван И."}
        second = await intake_brief(session, 1, BriefVariant.INDIVIDUAL, second_payload)
        return first.client_id, second.client_id

    first_client, second_client = asyncio.run(_with_db(scenario))
    assert first_client == second_client


def test_intake_missing_required_raises() -> None:
    async def scenario(session: AsyncSession) -> None:
        await intake_brief(session, 1, BriefVariant.INDIVIDUAL, {"full_name": "x"})

    with pytest.raises(BriefValidationError):
        asyncio.run(_with_db(scenario))


def test_intake_missing_tax_id_raises_individual() -> None:
    # Приём нового брифа обязан требовать ИНН — без него кабинет клиенту не
    # завести (решение 2026-08-25). Разбор уже сохранённых брифов (запуск
    # кампании, `launch_service.py`) этой проверке не подчиняется, см.
    # `services/brief_parser.py::parse_brief(require_tax_id=...)`.
    async def scenario(session: AsyncSession) -> None:
        payload = {k: v for k, v in VALID_INDIVIDUAL.items() if k != "tax_id"}
        await intake_brief(session, 1, BriefVariant.INDIVIDUAL, payload)

    with pytest.raises(BriefValidationError) as exc:
        asyncio.run(_with_db(scenario))
    assert "tax_id" in exc.value.missing


def test_intake_missing_tax_id_raises_community() -> None:
    # Обязательность — «везде»: и у бизнес-варианта тоже (не только у физлица).
    async def scenario(session: AsyncSession) -> None:
        payload = {k: v for k, v in VALID_COMMUNITY.items() if k != "tax_id"}
        await intake_brief(session, 1, BriefVariant.COMMUNITY, payload)

    with pytest.raises(BriefValidationError) as exc:
        asyncio.run(_with_db(scenario))
    assert "tax_id" in exc.value.missing


def test_intake_accepts_community_brief_with_tax_id() -> None:
    async def scenario(session: AsyncSession) -> tuple[int, str]:
        brief = await intake_brief(session, 1, BriefVariant.COMMUNITY, VALID_COMMUNITY)
        return brief.id, brief.variant

    brief_id, variant = asyncio.run(_with_db(scenario))
    assert brief_id >= 1
    assert variant == "community"
