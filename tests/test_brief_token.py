"""Тесты приёма брифа по токену инвайта (PR#4).

Связка `POST /api/v1/briefs` + `token`: успешный приём метит инвайт `received`
и связывает бриф; неизвестный токен → 404; повторный/неактивный → 409.
"""

from __future__ import annotations

import asyncio
from typing import Any

from core.app import create_app
from db.base import Base
from db.models import Account, Operator
from db.repositories import create_brief_invite, find_brief_invite_by_token, mark_invite_sent
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tests.test_brief_intake import VALID_INDIVIDUAL


async def _run(
    *,
    token: str | None,
    create_invite_row: bool,
    invite_sent: bool = True,
    posts: int = 1,
) -> tuple[list[int], str | None]:
    """Поднять приложение на sqlite, при необходимости создать инвайт, сделать POST(ы).

    Возвращает список статус-кодов ответов и итоговый статус инвайта (если был токен).
    """
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
        if create_invite_row and token is not None:
            session.add(Operator(id=10, account_id=1, telegram_id=555, full_name="Оп"))
            await session.commit()
            invite = await create_brief_invite(
                session, 1, 10, token, "individual", "email", "ivan@example.com", "email"
            )
            if invite_sent:
                await mark_invite_sent(session, invite.id)
        await session.commit()

    async def _override() -> Any:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)

    body: dict[str, Any] = {"variant": "individual", "payload": VALID_INDIVIDUAL}
    if token is not None:
        body["token"] = token

    codes: list[int] = []
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for _ in range(posts):
            resp = await client.post("/api/v1/briefs", json=body)
            codes.append(resp.status_code)

    invite_status: str | None = None
    if token is not None:
        async with maker() as session:
            found = await find_brief_invite_by_token(session, token)
            invite_status = found.status if found else None
    await engine.dispose()
    return codes, invite_status


def test_brief_with_valid_token_marks_received() -> None:
    codes, invite_status = asyncio.run(_run(token="tok123", create_invite_row=True))
    assert codes == [201]
    assert invite_status == "received"


def test_brief_unknown_token_returns_404() -> None:
    codes, _ = asyncio.run(_run(token="nonexistent", create_invite_row=False))
    assert codes == [404]


def test_brief_double_submit_returns_409() -> None:
    codes, invite_status = asyncio.run(_run(token="tok-dup", create_invite_row=True, posts=2))
    assert codes == [201, 409]
    assert invite_status == "received"


def test_brief_pending_invite_returns_409() -> None:
    # Инвайт создан, но не доставлен (pending) — приём по нему не допускаем.
    codes, _ = asyncio.run(_run(token="tok-pending", create_invite_row=True, invite_sent=False))
    assert codes == [409]


def test_brief_without_token_still_works() -> None:
    codes, _ = asyncio.run(_run(token=None, create_invite_row=False))
    assert codes == [201]
