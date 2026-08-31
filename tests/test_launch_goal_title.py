"""Перенос правила «запуск без креатива → цель Подписчики» из бота в ядро.

Правило раньше жило локальной константой в `bot/handlers/brief_card.py`
(`_NO_CREATIVE_GOAL_LABEL = GOAL_LABELS["subscribers"]`) — сам комментарий над ней
признавал нарушение принципа «бот не разбирает бриф сам» (CLAUDE.md §1.3). Теперь
цель считает ядро (`services.goals.NO_CREATIVE_GOAL`) и отдаёт готовым названием в
карточке брифа (`BriefCardOut.launch_goal_title`); бот только показывает.
"""

from __future__ import annotations

import asyncio
from typing import Any

from bot.api_client import AdAccountItem, BriefCard, BriefFieldItem
from bot.handlers import brief_card
from core.app import create_app
from db.base import Base
from db.models import Account, Brief, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from services.goals import NO_CREATIVE_GOAL, launch_goal_title
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def test_no_creative_goal_title_is_subscribers() -> None:
    assert launch_goal_title(NO_CREATIVE_GOAL) == "Подписчики"


def test_brief_card_out_exposes_launch_goal_title_field() -> None:
    from core.api.v1.briefs import BriefCardOut

    assert "launch_goal_title" in BriefCardOut.model_fields


def test_brief_card_view_exposes_launch_goal_title_field() -> None:
    from services.brief_view import BriefCardView

    assert "launch_goal_title" in BriefCardView.__annotations__


async def _with_client(scenario: Any) -> Any:
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
        session.add(Client(id=1, account_id=1, full_name="Вячеслав", email="v@example.com"))
        session.add(
            Brief(
                id=1,
                account_id=1,
                client_id=1,
                variant="individual",
                payload={"target_type": "личная страница"},
            )
        )
        await session.commit()

    async def _override() -> Any:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        result = await scenario(client)
    await engine.dispose()
    return result


def test_brief_card_endpoint_returns_launch_goal_title() -> None:
    """`GET /api/v1/briefs/{id}` — ядро само считает и отдаёт название цели,
    бот больше не решает это правило локально."""

    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.get("/api/v1/briefs/1")
        assert resp.status_code == 200, resp.text
        data: dict[str, Any] = resp.json()
        return data

    card = asyncio.run(_with_client(scenario))
    assert card["launch_goal_title"] == "Подписчики"


class _FakeMessage:
    def __init__(self) -> None:
        self.answers: list[tuple[str, Any]] = []

    async def answer(self, text: str, reply_markup: Any = None, **kwargs: Any) -> None:
        self.answers.append((text, reply_markup))


def _card(**over: Any) -> BriefCard:
    base: dict[str, Any] = {
        "brief_id": 9,
        "variant": "individual",
        "status": "received",
        "client_name": "Иван Петров",
        "client_email": None,
        "client_phone": None,
        "client_telegram": None,
        "fields": [BriefFieldItem(n=6, label="Ссылка на страницу VK", value="vk.com/ivan")],
        "has_creative": False,
        "campaign_status": None,
        "client_id": 42,
        "launch_goal_title": "Подписчики",
    }
    base.update(over)
    return BriefCard(**base)


def _account(**over: Any) -> AdAccountItem:
    base: dict[str, Any] = {
        "id": 3,
        "title": "Кабинет",
        "external_id": "10000003",
        "username": None,
        "token_tail": "abcd",
        "advertiser_kind": "owner",
        "advertiser_name": None,
        "advertiser_inn": None,
        "status": "active",
        "health": "healthy",
        "health_checked_at": None,
        "health_error": None,
        "balance_rub": None,
        "is_usable": True,
    }
    base.update(over)
    return AdAccountItem(**base)


def test_show_launch_confirmation_uses_the_goal_title_from_the_card() -> None:
    """Бот показывает то, что пришло от ядра, а не собственную константу —
    другое значение поля видно в тексте карточки."""
    message = _FakeMessage()
    card = _card(launch_goal_title="Особая цель из ядра")

    asyncio.run(
        brief_card._show_launch_confirmation(message, card, _account())  # type: ignore[arg-type]
    )

    text, _markup = message.answers[-1]
    assert "Особая цель из ядра" in text
