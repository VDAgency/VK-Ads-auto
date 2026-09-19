"""Тесты эндпоинта приёма креатива `POST /briefs/{id}/creative` (триггер запуска РК)."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

import pytest
import services.ad_accounts as ad_accounts
from config.settings import Settings
from core.app import create_app
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, Brief, Client
from db.repositories import get_creative_for_brief
from db.session import get_session
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from services.ad_accounts import add_account
from services.vk_identity import VkIdentity
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

_VALID = {
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
_IMAGE_B64 = base64.b64encode(b"\xff\xd8\xff\x00" * 100).decode("ascii")

# Кабинет оператора для запуска (spec 2026-07-27 §9): токен и кабинет теперь только
# из базы, поэтому тест заводит один активный кабинет с ключом шифрования ниже.
TOKEN = "fake-access-token-for-tests-0000000000000000"
_KEY = Fernet.generate_key().decode()
IDENTITY = VkIdentity(
    external_id="10000001",
    username="a1b2c3d4e5@agency_client",
    title="Кабинет «Пример»",
    status="active",
)


def _settings(**overrides: object) -> Settings:
    """Настройки с ключом шифрования кабинетов (без него не расшифровать токен из БД)."""
    values: dict[str, object] = {"_env_file": None, "vk_ads_secret_key": SecretStr(_KEY)}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _stub_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Детерминизм: креативы во временный каталог, запуск — заглушка (без vk_live_campaigns)."""
    settings = _settings(creatives_dir=str(tmp_path))
    monkeypatch.setattr("services.creative_store.get_settings", lambda: settings)
    monkeypatch.setattr("services.launch_service.get_settings", lambda: settings)


@pytest.fixture(autouse=True)
def _mock_vk_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """VK всегда отвечает одной и той же личностью — кабинет в БД один и активен."""

    async def identity(token: str, **_: object) -> VkIdentity:
        return IDENTITY

    async def balance(token: str, **_: object) -> str | None:
        return None

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)


async def _with_client(
    scenario: Callable[[AsyncClient], Awaitable[T]],
    *,
    seed: bool = True,
    payload: dict[str, str] | None = None,
    after: Callable[[AsyncSession], Awaitable[None]] | None = None,
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
        if seed:
            session.add(Client(id=1, account_id=1, full_name="Вячеслав", email="v@example.com"))
            session.add(
                Brief(
                    id=1,
                    account_id=1,
                    client_id=1,
                    variant="individual",
                    payload=payload or dict(_VALID),
                )
            )
        await session.commit()
        if seed:
            # Единственный активный кабинет: запуск без явного `ad_account_id`
            # резолвится в него (`resolve_default_account`).
            await add_account(session, 1, TOKEN, settings=_settings())
            await session.commit()

    async def _override() -> Any:
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        result = await scenario(client)
    if after is not None:
        async with maker() as session:
            await after(session)
    await engine.dispose()
    return result


def test_upload_creative_prepares_campaign_and_marks_card() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.post(
            "/api/v1/briefs/1/creative",
            json={
                "media_b64": _IMAGE_B64,
                "media_type": "photo",
                "width": 800,
                "height": 800,
                "title": "Заголовок",
                "body": "Текст объявления",
            },
        )
        assert resp.status_code == 201, resp.text
        card = await client.get("/api/v1/briefs/1")
        return {"upload": resp.json(), "card": card.json()}

    result = asyncio.run(_with_client(scenario))
    assert result["upload"]["campaign_status"] == "prepared"
    assert "подготовлена" in result["upload"]["message"]
    # Карточка теперь отражает загруженный креатив и статус кампании.
    assert result["card"]["has_creative"] is True
    assert result["card"]["campaign_status"] == "prepared"


def test_upload_creative_rejects_small_image() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/briefs/1/creative",
            json={"media_b64": _IMAGE_B64, "media_type": "photo", "width": 100, "height": 100},
        )
        return resp.status_code

    # Минимальный размер 600×600 не выдержан → 422.
    assert asyncio.run(_with_client(scenario)) == 422


def test_upload_creative_invalid_base64() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/briefs/1/creative",
            json={"media_b64": "not-base64!!!", "media_type": "photo", "width": 800, "height": 800},
        )
        return resp.status_code

    assert asyncio.run(_with_client(scenario)) == 422


def test_upload_creative_404_when_brief_missing() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/briefs/999/creative",
            json={"media_b64": _IMAGE_B64, "media_type": "photo", "width": 800, "height": 800},
        )
        return resp.status_code

    assert asyncio.run(_with_client(scenario, seed=False)) == 404


def test_upload_creative_with_hashtags_appends_them_to_body() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.post(
            "/api/v1/briefs/1/creative",
            json={
                "media_b64": _IMAGE_B64,
                "media_type": "photo",
                "width": 800,
                "height": 800,
                "title": "Заголовок",
                "body": "Текст объявления",
                "hashtags": "скидка, Акция",
            },
        )
        return {"status": resp.status_code, "body": resp.json()}

    saved_body: dict[str, str | None] = {}

    async def after(session: AsyncSession) -> None:
        creative = await get_creative_for_brief(session, 1, 1)
        assert creative is not None
        saved_body["value"] = creative.body

    result = asyncio.run(_with_client(scenario, after=after))
    assert result["status"] == 201, result["body"]
    assert saved_body["value"] == "Текст объявления\n#скидка #Акция"


def test_upload_creative_without_hashtags_behaves_as_before() -> None:
    async def scenario(client: AsyncClient) -> int:
        resp = await client.post(
            "/api/v1/briefs/1/creative",
            json={
                "media_b64": _IMAGE_B64,
                "media_type": "photo",
                "width": 800,
                "height": 800,
                "title": "Заголовок",
                "body": "Текст объявления",
            },
        )
        return resp.status_code

    assert asyncio.run(_with_client(scenario)) == 201


def test_upload_creative_rejects_invalid_hashtag() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.post(
            "/api/v1/briefs/1/creative",
            json={
                "media_b64": _IMAGE_B64,
                "media_type": "photo",
                "width": 800,
                "height": 800,
                "title": "Заголовок",
                "body": "Текст объявления",
                "hashtags": "#a-b",
            },
        )
        return {"status": resp.status_code, "detail": resp.json()["detail"]}

    result = asyncio.run(_with_client(scenario))
    assert result["status"] == 422
    assert result["detail"] == "hashtags_invalid_tag"


def test_upload_creative_hashtags_not_supported_for_surface_without_text() -> None:
    async def scenario(client: AsyncClient) -> dict[str, Any]:
        resp = await client.post(
            "/api/v1/briefs/1/creative",
            json={
                "media_b64": _IMAGE_B64,
                "media_type": "photo",
                "width": 800,
                "height": 800,
                "title": "Заголовок",
                "body": "Текст объявления",
                "hashtags": "промо",
            },
        )
        return {"status": resp.status_code, "detail": resp.json()["detail"]}

    # Продвижение поста сообщества — площадка без текстового слота
    # (`integrations.vk_surfaces.VK_POST_COMMUNITY.needs_creative is False`).
    payload = dict(_VALID)
    payload["target_type"] = "пост сообщества"

    result = asyncio.run(_with_client(scenario, payload=payload))
    assert result["status"] == 422
    assert result["detail"] == "hashtags_not_supported"
