"""Тесты веб-эндпоинта предпросмотра запуска
(`GET /api/v1/admin/briefs/{id}/launch-preview`, `core/api/v1/admin_operations.py`).

До этого эндпоинта веб не показывал карточку подтверждения запуска — кнопка
«Отправить и запустить кампанию» стреляла бы сразу, без сверки клиента/ИНН/
бюджета/кабинета, которую делает бот (`bot/handlers/creative.py::
render_launch_confirmation`) перед тем же самым действием. Тесты проверяют,
что веб-сводка несёт ту же информацию и те же предупреждения (баланс, чужой
кабинет), не выполняя запуск (эндпоинт только читает).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

import pytest
import services.ad_accounts as ad_accounts
import services.launch_service as launch_service
from config.settings import Settings, get_settings
from core.app import create_app
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Brief, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from services.ad_accounts import add_account
from services.admin_auth import generate_admin_session
from services.goals import NO_CREATIVE_GOAL, launch_goal_title
from services.vk_identity import VkIdentity
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-access-token-for-tests-0000000000000000"
_KEY = Fernet.generate_key().decode()
_SECRET = get_settings().secret_key.get_secret_value()

BRIEF_CLIENT_ID = 100
OTHER_CLIENT_ID = 7

# Вариант individual (services/brief_fields.py): "Бюджет" -> daily_budget = 30000/30 = 1000.
BRIEF_PAYLOAD = {
    "full_name": "Вячеслав",
    "object_url": "https://vk.com/id1",
    "email": "v@example.com",
    "phone": "+79990000000",
    "audience_description": "молодёжь Самары",
    "geo": "Самара",
    "budget": "30000",
    "term": "1 месяц",
    "target_type": "личная страница",
}

IDENTITY = VkIdentity(
    external_id="10000001",
    username="a1b2c3d4e5@agency_client",
    title="Кабинет",
    status="active",
)

_DEFAULT_BALANCE = "50000.00"


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {"_env_file": None, "vk_ads_secret_key": SecretStr(_KEY)}
    base.update(over)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _mock_vk_and_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """VK замокан (кабинет с достаточным по умолчанию балансом), ключ шифрования подставлен."""

    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return _DEFAULT_BALANCE

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)
    monkeypatch.setattr(ad_accounts, "get_settings", lambda: _settings())
    # `launch_service.launch_preview` берёт настройки своим собственным `get_settings()`,
    # если роутер не передал явные — тем же ключом, что и у `add_account` выше, иначе
    # расшифровка токена кабинета сломается на ровном месте (свежий ключ на вызов).
    monkeypatch.setattr(launch_service, "get_settings", lambda: _settings())


async def _with_admin(
    scenario: Callable[[AsyncClient, async_sessionmaker[AsyncSession]], Awaitable[T]],
    *,
    authed: bool = True,
) -> T:
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
        session.add(
            Client(
                id=BRIEF_CLIENT_ID, account_id=1, full_name="Клиент брифа", email="c@example.com"
            )
        )
        session.add(
            Client(
                id=OTHER_CLIENT_ID,
                account_id=1,
                full_name="Другой клиент",
                email="other@example.com",
            )
        )
        session.add(
            Brief(
                id=1,
                account_id=1,
                client_id=BRIEF_CLIENT_ID,
                variant="individual",
                status="received",
                payload=dict(BRIEF_PAYLOAD),
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
        if authed:
            client.cookies.set("admin_session", generate_admin_session(555, _SECRET))
        result = await scenario(client, maker)
    await engine.dispose()
    return result


async def _add_cabinet(maker: async_sessionmaker[AsyncSession], *, client_id: int | None) -> int:
    async with maker() as session:
        view = await add_account(session, 1, TOKEN, client_id=client_id, settings=_settings())
        await session.commit()
        return view.id


def test_launch_preview_requires_admin_session() -> None:
    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> int:
        resp = await client.get("/api/v1/admin/briefs/1/launch-preview")
        return resp.status_code

    assert asyncio.run(_with_admin(scenario, authed=False)) == 401


def test_launch_preview_returns_summary_for_the_bound_account() -> None:
    async def scenario(
        client: AsyncClient, maker: async_sessionmaker[AsyncSession]
    ) -> dict[str, Any]:
        ad_account_id = await _add_cabinet(maker, client_id=BRIEF_CLIENT_ID)
        resp = await client.get(
            "/api/v1/admin/briefs/1/launch-preview", params={"ad_account_id": ad_account_id}
        )
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["client_name"] == "Клиент брифа"
    assert data["object_url"] == "https://vk.com/id1"
    assert data["budget_text"] == "30000"
    assert data["term_text"] == "1 месяц"
    assert data["ad_account_client_id"] == BRIEF_CLIENT_ID
    assert data["ad_account_client_name"] == "Клиент брифа"
    assert data["daily_budget_rub"] == 1000.0
    assert data["client_mismatch"] is False
    assert data["balance_below_daily_budget"] is False
    # Токен кабинета ни в каком виде не попадает в ответ.
    assert TOKEN not in str(data)


def test_launch_preview_warns_when_balance_below_daily_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def low_balance(token: str, **_: object) -> str | None:
        return "500.00"  # меньше дневного лимита 1000 ₽

    monkeypatch.setattr(ad_accounts, "fetch_balance", low_balance)

    async def scenario(
        client: AsyncClient, maker: async_sessionmaker[AsyncSession]
    ) -> dict[str, Any]:
        ad_account_id = await _add_cabinet(maker, client_id=BRIEF_CLIENT_ID)
        resp = await client.get(
            "/api/v1/admin/briefs/1/launch-preview", params={"ad_account_id": ad_account_id}
        )
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["balance_below_daily_budget"] is True


def test_launch_preview_does_not_warn_when_balance_covers_the_daily_budget() -> None:
    """Баланс по умолчанию (50 000 ₽) выше дневного лимита (1000 ₽) — без предупреждения."""

    async def scenario(
        client: AsyncClient, maker: async_sessionmaker[AsyncSession]
    ) -> dict[str, Any]:
        ad_account_id = await _add_cabinet(maker, client_id=BRIEF_CLIENT_ID)
        resp = await client.get(
            "/api/v1/admin/briefs/1/launch-preview", params={"ad_account_id": ad_account_id}
        )
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["balance_below_daily_budget"] is False


def test_launch_preview_flags_a_cabinet_bound_to_another_client() -> None:
    """Кабинет закреплён за ДРУГИМ клиентом — «деньги спишутся не с того счёта»."""

    async def scenario(
        client: AsyncClient, maker: async_sessionmaker[AsyncSession]
    ) -> dict[str, Any]:
        ad_account_id = await _add_cabinet(maker, client_id=OTHER_CLIENT_ID)
        resp = await client.get(
            "/api/v1/admin/briefs/1/launch-preview", params={"ad_account_id": ad_account_id}
        )
        # Предпросмотр только предупреждает — не блокирует и не бросает 409.
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["client_mismatch"] is True
    assert data["ad_account_client_id"] == OTHER_CLIENT_ID


def test_launch_preview_does_not_flag_a_common_cabinet() -> None:
    """Общий кабинет (`client_id=None`) подходит любому брифу — не признак ошибки."""

    async def scenario(
        client: AsyncClient, maker: async_sessionmaker[AsyncSession]
    ) -> dict[str, Any]:
        ad_account_id = await _add_cabinet(maker, client_id=None)
        resp = await client.get(
            "/api/v1/admin/briefs/1/launch-preview", params={"ad_account_id": ad_account_id}
        )
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["client_mismatch"] is False


def test_launch_preview_missing_brief_returns_404_not_500() -> None:
    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> int:
        resp = await client.get("/api/v1/admin/briefs/999/launch-preview")
        return resp.status_code

    assert asyncio.run(_with_admin(scenario)) == 404


def test_launch_preview_no_ad_account_returns_409() -> None:
    """Кабинетов нет вовсе — тот же код, что и у реального запуска, не 500."""

    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> int:
        resp = await client.get("/api/v1/admin/briefs/1/launch-preview")
        return resp.status_code

    assert asyncio.run(_with_admin(scenario)) == 409


def test_launch_preview_without_goal_shows_no_creative_goal() -> None:
    """Без `goal` сводка ведёт себя как раньше: цель запуска без креатива."""

    async def scenario(
        client: AsyncClient, maker: async_sessionmaker[AsyncSession]
    ) -> dict[str, Any]:
        ad_account_id = await _add_cabinet(maker, client_id=BRIEF_CLIENT_ID)
        resp = await client.get(
            "/api/v1/admin/briefs/1/launch-preview", params={"ad_account_id": ad_account_id}
        )
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["goal_title"] == launch_goal_title(NO_CREATIVE_GOAL)


def test_launch_preview_with_explicit_goal_shows_that_goal() -> None:
    """Оператор выбрал цель на отдельном шаге — сводка обязана показать именно её,
    а не всегда «Подписчики» (сценарий с креативом)."""

    async def scenario(
        client: AsyncClient, maker: async_sessionmaker[AsyncSession]
    ) -> dict[str, Any]:
        ad_account_id = await _add_cabinet(maker, client_id=BRIEF_CLIENT_ID)
        resp = await client.get(
            "/api/v1/admin/briefs/1/launch-preview",
            params={"ad_account_id": ad_account_id, "goal": "messages"},
        )
        assert resp.status_code == 200, resp.text
        body: dict[str, Any] = resp.json()
        return body

    data = asyncio.run(_with_admin(scenario))
    assert data["goal_title"] == launch_goal_title("messages")
    assert data["goal_title"] != launch_goal_title(NO_CREATIVE_GOAL)


def test_launch_preview_unknown_goal_returns_422_not_500() -> None:
    """Несуществующая/нереализованная цель — понятный отказ, не молчаливая подмена."""

    async def scenario(client: AsyncClient, maker: async_sessionmaker[AsyncSession]) -> Any:
        ad_account_id = await _add_cabinet(maker, client_id=BRIEF_CLIENT_ID)
        resp = await client.get(
            "/api/v1/admin/briefs/1/launch-preview",
            params={"ad_account_id": ad_account_id, "goal": "not_a_real_goal"},
        )
        return resp

    resp = asyncio.run(_with_admin(scenario))
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "goal_not_supported"
