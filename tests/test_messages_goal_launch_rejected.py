"""Отказ для непроверенной цели «Сообщения» — честный 422, а не 500 (code review).

`tests/test_messages_goal.py` закрепляет, что площадка «Сообщения» заведена, но
не предлагается клиенту/оператору через интерфейсы (кнопка, `subscription_targets`).
Здесь — другая часть той же дыры: раньше `services.mapping.build_campaign_spec`
бросал голый `ValueError`, если бриф всё-таки пришёл с `target_type` «написать
сообщение» (веб-форма его дизейблит, но прямой HTTP-запрос — нет). Эту ошибку
никто не ловил: она долетала до оператора как необработанное исключение (500),
а бот показывал «Сервис временно недоступен» — неправду, сервис был доступен,
просто цель не реализована.

Тесты здесь: бриф с этой площадкой спокойно принимается (приём цель не проверяет),
а вот попытка запустить кампанию — что через приём креатива, что через запуск
готового поста без креатива — обязана вернуть 422 `goal_not_supported`, причём
в ОБОИХ роутерах (публичный `/api/v1/briefs`, admin `/api/v1/admin/briefs`),
и не должна успеть дойти до площадки VK.
"""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
import services.ad_accounts as ad_accounts
import services.launch_service as launch_service
from config.settings import Settings
from core.app import create_app
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Brief, Client
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from services.ad_accounts import add_account
from services.admin_auth import generate_admin_session
from services.launch_service import UnsupportedGoalError, launch_from_creative
from services.mapping import UnsupportedBriefGoalError
from services.vk_identity import VkIdentity
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-access-token-for-tests-0000000000000000"
_KEY = Fernet.generate_key().decode()
_ADMIN_SECRET = Settings(_env_file=None).secret_key.get_secret_value()
IDENTITY = VkIdentity("10000001", "a1b2c3d4e5@agency_client", "Кабинет «Пример»", "active")

_IMAGE_B64 = base64.b64encode(b"\xff\xd8\xff\x00" * 100).decode("ascii")

# Бриф с площадкой «сообщения» — веб-форма её дизейблит, но прямому HTTP-запросу
# в приём брифа это не мешает: `intake_brief` цель не проверяет вовсе.
MESSAGES_PAYLOAD = {
    "full_name": "Вячеслав",
    "object_url": "https://vk.com/community1",
    "email": "v@example.com",
    "phone": "+79990000000",
    "audience_description": "молодёжь Самары",
    "geo": "Самара",
    "budget": "30000",
    "term": "1 месяц",
    "target_type": "написать сообщение",
}


def _settings(**over: Any) -> Settings:
    base: dict[str, Any] = {"_env_file": None, "vk_ads_secret_key": SecretStr(_KEY)}
    base.update(over)
    return Settings(**base)


class RecordingAdapter:
    """Заглушка VK-канала: если запуск дойдёт до неё, тест это заметит."""

    last_spec: Any = None

    def __init__(self, token: str) -> None:
        self.token = token

    async def health_check(self) -> bool:
        return True

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return "created-cabinet"

    async def create_campaign_from_spec(self, cabinet_id: str, spec: Any, **kwargs: Any) -> str:
        RecordingAdapter.last_spec = spec
        return "vk-campaign-1"

    async def launch(self, campaign_id: str) -> None:
        return None

    async def get_status(self, campaign_id: str) -> str:
        return "active"

    async def stop(self, campaign_id: str) -> None:
        return None


class FakeVkAdapter(RecordingAdapter):
    def __init__(self, access_token: SecretStr, **_: object) -> None:
        super().__init__(access_token.get_secret_value())


@pytest.fixture(autouse=True)
def _mock_vk_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """VK всегда отвечает одной и той же личностью — кабинет в БД один и активен."""

    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return None

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)


# --- сервисный уровень: launch_from_creative не должен звать адаптер -----------


@pytest.fixture(autouse=True)
def _mock_settings_for_service_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ad_accounts, "get_settings", lambda: _settings())
    monkeypatch.setattr(launch_service, "VkApiAdapter", FakeVkAdapter)
    monkeypatch.setattr(
        launch_service, "save_creative", lambda *a, **k: "creative.jpg", raising=False
    )


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
        session.add(Client(id=100, account_id=1, full_name="Вячеслав", email="v@example.com"))
        session.add(
            Brief(
                id=500,
                account_id=1,
                client_id=100,
                variant="individual",
                status="received",
                payload=dict(MESSAGES_PAYLOAD),
            )
        )
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def test_launch_from_creative_rejects_messages_goal_without_reaching_the_adapter() -> None:
    """Раньше это был необработанный `ValueError` (→ 500). Теперь — типизированный
    `UnsupportedGoalError`, и площадка VK ни разу не вызывается: `create_campaign_from_spec`
    не сработал (`RecordingAdapter.last_spec` остаётся `None`)."""
    RecordingAdapter.last_spec = None

    async def scenario(session: AsyncSession) -> None:
        cabinet_view = await add_account(session, 1, TOKEN, settings=_settings())
        await session.commit()

        with pytest.raises(UnsupportedGoalError):
            await launch_from_creative(
                session,
                1,
                500,
                "photo",
                "creative.jpg",
                "Заголовок",
                "Текст",
                settings=_settings(),
                ad_account_id=cabinet_view.id,
            )

    asyncio.run(_with_db(scenario))
    assert RecordingAdapter.last_spec is None


def test_launch_service_wraps_mapping_error_not_the_bare_one() -> None:
    """Ошибка из `services.mapping` (`UnsupportedBriefGoalError`) не долетает наружу
    как есть — `launch_from_creative` транслирует её в `UnsupportedGoalError`,
    который уже умеют ловить роутеры (`goal_not_supported`, 422)."""

    async def scenario(session: AsyncSession) -> BaseException:
        cabinet_view = await add_account(session, 1, TOKEN, settings=_settings())
        await session.commit()
        try:
            await launch_from_creative(
                session,
                1,
                500,
                "photo",
                "creative.jpg",
                "Заголовок",
                "Текст",
                settings=_settings(),
                ad_account_id=cabinet_view.id,
            )
        except Exception as exc:  # noqa: BLE001 — тест ловит ровно то, что бросил сервис
            return exc
        raise AssertionError("launch_from_creative did not raise")

    exc = asyncio.run(_with_db(scenario))
    assert isinstance(exc, UnsupportedGoalError)
    assert not isinstance(exc, UnsupportedBriefGoalError)
    assert not isinstance(exc, ValueError)


# --- HTTP-уровень: оба роутера отвечают 422, а не 500 --------------------------


@pytest.fixture(autouse=True)
def _stub_settings_for_http_level(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Детерминизм для HTTP-тестов: креативы во временный каталог (как в других тестах)."""
    settings = _settings(creatives_dir=str(tmp_path))
    monkeypatch.setattr("services.creative_store.get_settings", lambda: settings)
    monkeypatch.setattr("services.launch_service.get_settings", lambda: settings)


async def _with_client(
    scenario: Callable[[AsyncClient], Awaitable[T]],
    *,
    seed_ad_account: bool = True,
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
        session.add(Client(id=1, account_id=1, full_name="Вячеслав", email="v@example.com"))
        session.add(
            Brief(id=1, account_id=1, client_id=1, variant="individual", payload=MESSAGES_PAYLOAD)
        )
        await session.commit()
        if seed_ad_account:
            await add_account(session, 1, TOKEN, settings=_settings())
            await session.commit()

    async def _override() -> Any:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        client.cookies.set("admin_session", generate_admin_session(555, _ADMIN_SECRET))
        result = await scenario(client)
    await engine.dispose()
    return result


def test_public_brief_intake_accepts_messages_target_type() -> None:
    """Приём брифа не проверяет цель вовсе — это подтверждает описанную в code
    review дыру: непроверенная площадка спокойно долетает до сохранения."""

    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/briefs",
            json={"variant": "individual", "payload": MESSAGES_PAYLOAD},
        )
        return resp.status_code

    assert asyncio.run(_with_client(scenario, seed_ad_account=False)) == 201


def test_public_creative_upload_for_messages_goal_is_422_not_500() -> None:
    async def scenario(client: AsyncClient) -> tuple[int, Any]:
        resp = await client.post(
            "/api/v1/briefs/1/creative",
            json={
                "media_b64": _IMAGE_B64,
                "media_type": "photo",
                "width": 800,
                "height": 800,
                "title": "Заголовок",
                "body": "Текст",
            },
        )
        return resp.status_code, resp.json()

    code, body = asyncio.run(_with_client(scenario))
    assert code == 422, body
    assert body["detail"] == "goal_not_supported"


def test_public_launch_without_creative_for_messages_goal_is_422_not_500() -> None:
    """Тот же путь, но продвижение готового поста (без креатива) — `/launch`.
    До фикса у этого эндпоинта не было `except UnsupportedGoalError` вовсе, поэтому
    ошибка мапинга долетала до FastAPI необработанной (500)."""

    async def scenario(client: AsyncClient) -> tuple[int, Any]:
        resp = await client.post("/api/v1/briefs/1/launch", json={})
        return resp.status_code, resp.json()

    code, body = asyncio.run(_with_client(scenario))
    assert code == 422, body
    assert body["detail"] == "goal_not_supported"


def test_admin_creative_upload_for_messages_goal_is_422_not_500() -> None:
    async def scenario(client: AsyncClient) -> tuple[int, Any]:
        resp = await client.post(
            "/api/v1/admin/briefs/1/creative",
            json={
                "media_b64": _IMAGE_B64,
                "media_type": "photo",
                "width": 800,
                "height": 800,
                "title": "Заголовок",
                "body": "Текст",
            },
        )
        return resp.status_code, resp.json()

    code, body = asyncio.run(_with_client(scenario))
    assert code == 422, body
    assert body["detail"] == "goal_not_supported"


def test_public_upload_creative_404_when_brief_missing_still_works() -> None:
    """Регресс: 404 для отсутствующего брифа проверяется раньше цели, порядок не сломан."""

    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/briefs/999/creative",
            json={"media_b64": _IMAGE_B64, "media_type": "photo", "width": 800, "height": 800},
        )
        return resp.status_code

    assert asyncio.run(_with_client(scenario, seed_ad_account=False)) == 404
