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
