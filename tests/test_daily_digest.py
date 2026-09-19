"""Тесты ежедневной сводки оператору (`services.daily_digest`, задача 3А).

`render_digest` — чистое форматирование, без БД. `collect_digest` проверяется
на SQLite-фикстурах по образцу `tests/test_stats_sync.py`. Большинство тестов
подменяют сам синк («замокать синк... так же, как в существующих тестах
синка», брифинг задачи) — monkeypatch `services.daily_digest.
sync_campaign_stats` — чтобы не тянуть в каждый тест ещё и живой выбор
адаптера/канала, уже покрытый `test_stats_sync.py`. Один тест (ревью, фикс-
раунд 1) намеренно идёт РЕАЛЬНЫМ `sync_campaign_stats` с фейковым
`PlatformAdapter` (тот же приём, что и в `test_stats_sync.py`) — проверяет,
что статус кампании в отчёте — результат сегодняшнего синка, а не значение,
случайно унаследованное из identity-map сессии.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any, TypeVar

import pytest
import services.ad_accounts as ad_accounts
import services.daily_digest as daily_digest_module
import services.launch_service as launch_service
from config.settings import Settings
from cryptography.fernet import Fernet
from db.base import Base
from db.models import Account, AdAccount, Brief, Cabinet, Campaign, Client, Stat
from db.repositories import list_active_campaigns
from integrations.adapter import PlatformAdapter
from pydantic import SecretStr
from services.ad_accounts import add_account
from services.daily_digest import DigestReport, DigestRow, collect_digest, render_digest
from services.vk_identity import VkIdentity
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

T = TypeVar("T")

TOKEN = "fake-access-token-for-tests-0000000000000000"


def _settings() -> Settings:
    return Settings(_env_file=None)


def _live_settings() -> Settings:
    """Настройки с ключом шифрования и разрешённым боевым каналом (для кабинетов)."""
    return Settings(
        _env_file=None,
        vk_ads_secret_key=SecretStr(Fernet.generate_key().decode()),
        vk_live_campaigns=True,
    )


# --- render_digest: чистое форматирование -------------------------------------------


def _row(**over: Any) -> DigestRow:
    base: dict[str, Any] = {
        "title": "Подписчики · Иван Иванов",
        "cabinet": "Кабинет «Основной»",
        "status": "launched",
        "shows": 1000.0,
        "clicks": 50.0,
        "spent": 500.0,
        "results": 10.0,
        "stale": False,
    }
    base.update(over)
    return DigestRow(**base)


def test_render_digest_empty_mentions_no_campaigns_and_date() -> None:
    report = DigestReport(rows=[], day=date(2026, 9, 19))
    text = render_digest(report)
    assert "Активных кампаний нет" in text
    assert "19.09.2026" in text


def test_render_digest_single_campaign_has_all_metrics() -> None:
    report = DigestReport(rows=[_row()], day=date(2026, 9, 19))
    text = render_digest(report)
    assert "Подписчики · Иван Иванов" in text
    assert "показы 1000" in text
    assert "клики 50" in text
    assert "расход 500" in text
    assert "результаты 10" in text
    assert "CTR 5.0%" in text  # 50/1000*100
    assert "CPC 10.0 ₽" in text  # 500/50
    assert "CPL 50.0 ₽" in text  # 500/10


def test_render_digest_groups_by_cabinet_with_totals() -> None:
    rows = [
        _row(title="Кампания A", cabinet="Кабинет 1", spent=100.0, results=5.0),
        _row(title="Кампания B", cabinet="Кабинет 2", spent=200.0, results=7.0),
    ]
    report = DigestReport(rows=rows, day=date(2026, 9, 19))
    text = render_digest(report)

    assert "Кабинет «Кабинет 1»" in text
    assert "Кабинет «Кабинет 2»" in text
    assert text.index("Кабинет «Кабинет 1»") < text.index("Кампания A")
    assert text.index("Кабинет «Кабинет 2»") < text.index("Кампания B")
    assert "Итого: расход 300 ₽, результатов 12." in text


def test_render_digest_marks_stale_row() -> None:
    report = DigestReport(rows=[_row(stale=True)], day=date(2026, 9, 19))
    text = render_digest(report)
    assert "данные не обновились" in text


def test_render_digest_zero_clicks_does_not_raise_and_shows_dash() -> None:
    report = DigestReport(rows=[_row(clicks=0.0, results=0.0)], day=date(2026, 9, 19))
    text = render_digest(report)  # не должно упасть ZeroDivisionError
    assert "CPC —" in text
    assert "CPL —" in text


# --- collect_digest: сборка отчёта по БД --------------------------------------------


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
        session.add(Client(id=1, account_id=1, full_name="Иван Иванов"))
        session.add(Brief(id=1, account_id=1, client_id=1, variant="individual", payload={}))
        await session.commit()
        result = await scenario(session)
    await engine.dispose()
    return result


def _campaign(
    campaign_id: int,
    *,
    account_id: int = 1,
    status: str = "launched",
    external_id: str | None = "ext-1",
    cabinet_id: int | None = None,
    ad_account_id: int | None = None,
    spec_name: str = "Подписчики · Иван Иванов",
) -> Campaign:
    return Campaign(
        id=campaign_id,
        account_id=account_id,
        brief_id=1,
        client_id=1,
        cabinet_id=cabinet_id,
        ad_account_id=ad_account_id,
        status=status,
        objective="socialengagement",
        external_id=external_id,
        spec_json={"name": spec_name},
    )


def _mock_sync(outcomes: dict[int, str]) -> Callable[..., Awaitable[dict[int, str]]]:
    async def fake(session: AsyncSession, account_id: int, **_: Any) -> dict[int, str]:
        return dict(outcomes)

    return fake


def test_collect_digest_builds_row_with_title_and_cabinet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({1: "ok"}))

    async def scenario(session: AsyncSession) -> DigestReport:
        session.add(
            AdAccount(
                id=1,
                account_id=1,
                title="Кабинет «Ромашка»",
                external_id="100500",
                token_tail="",
            )
        )
        session.add(_campaign(1, external_id="ext-1", ad_account_id=1))
        session.add(
            Stat(account_id=1, campaign_id="ext-1", shows=100, clicks=5, spent=250, results=10)
        )
        await session.commit()
        return await collect_digest(session, 1, _settings())

    report = asyncio.run(_with_db(scenario))
    assert len(report.rows) == 1
    row = report.rows[0]
    assert row.title == "Подписчики · Иван Иванов"
    assert row.cabinet == "Кабинет «Ромашка»"
    assert row.status == "launched"
    assert row.shows == 100
    assert row.clicks == 5
    assert row.spent == 250
    assert row.results == 10
    assert row.stale is False


def test_collect_digest_campaign_without_ad_account_shows_dash_cabinet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({1: "ok"}))

    async def scenario(session: AsyncSession) -> DigestReport:
        session.add(_campaign(1, external_id="ext-1", ad_account_id=None))
        await session.commit()
        return await collect_digest(session, 1, _settings())

    report = asyncio.run(_with_db(scenario))
    assert report.rows[0].cabinet == "—"


def test_collect_digest_campaign_without_any_stat_row_is_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кампания без единого сохранённого среза — честные нули, а не выдумка."""
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({1: "ok"}))

    async def scenario(session: AsyncSession) -> DigestReport:
        session.add(_campaign(1, external_id="ext-1"))
        await session.commit()
        return await collect_digest(session, 1, _settings())

    report = asyncio.run(_with_db(scenario))
    row = report.rows[0]
    assert row.shows == 0.0
    assert row.clicks == 0.0
    assert row.spent == 0.0
    assert row.results == 0.0


def test_collect_digest_uses_latest_stat_slice_not_sum(monkeypatch: pytest.MonkeyPatch) -> None:
    """VK отдаёт накопительный итог — берём последний срез, не сумму (как cabinet_stats)."""
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({1: "ok"}))

    async def scenario(session: AsyncSession) -> DigestReport:
        session.add(_campaign(1, external_id="ext-1"))
        session.add(
            Stat(
                account_id=1,
                campaign_id="ext-1",
                shows=100,
                clicks=10,
                spent=50,
                captured_at=datetime(2026, 9, 19, 6, 0, tzinfo=UTC),
            )
        )
        session.add(
            Stat(
                account_id=1,
                campaign_id="ext-1",
                shows=200,
                clicks=20,
                spent=90,
                captured_at=datetime(2026, 9, 19, 7, 0, tzinfo=UTC),
            )
        )
        await session.commit()
        return await collect_digest(session, 1, _settings())

    report = asyncio.run(_with_db(scenario))
    row = report.rows[0]
    assert row.shows == 200.0
    assert row.spent == 90.0


def test_collect_digest_marks_failed_sync_as_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({1: "error"}))

    async def scenario(session: AsyncSession) -> DigestReport:
        session.add(_campaign(1, external_id="ext-1"))
        await session.commit()
        return await collect_digest(session, 1, _settings())

    report = asyncio.run(_with_db(scenario))
    assert report.rows[0].stale is True


def test_collect_digest_day_uses_moscow_offset_across_midnight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """22:30 UTC — уже за полночь по Москве (UTC+3): дата отчёта обязана стать
    завтрашней. Прежний тест сравнивал с `datetime.now()` той же формулой, что
    и сама реализация, — тавтология, которая не поймала бы ошибку в переводе
    часового пояса. Теперь время внутрь передаётся явно (шов `now`)."""
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({}))

    async def scenario(session: AsyncSession) -> DigestReport:
        return await collect_digest(
            session, 1, _settings(), now=datetime(2026, 9, 18, 22, 30, tzinfo=UTC)
        )

    report = asyncio.run(_with_db(scenario))
    assert report.day == date(2026, 9, 19)


def test_collect_digest_empty_when_no_active_campaigns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(daily_digest_module, "sync_campaign_stats", _mock_sync({}))

    async def scenario(session: AsyncSession) -> DigestReport:
        return await collect_digest(session, 1, _settings())

    report = asyncio.run(_with_db(scenario))
    assert report.rows == []


# --- ревью, фикс-раунд 1: статус строки — результат РЕАЛЬНОГО сегодняшнего --
# синка, а не значение, унаследованное из identity-map сессии ----------------


class _StatusFlipAdapter(PlatformAdapter):
    """Настоящий путь `sync_campaign_stats` (не подмена самого синка): площадка
    отвечает `blocked` посреди прогона — кампания обязана стать `stopped`."""

    def __init__(self, access_token: SecretStr, **_: object) -> None:
        self.token = access_token.get_secret_value()

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return "cab"

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        return "camp"

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        return "creative"

    async def launch(self, campaign_id: str) -> None:
        return None

    async def health_check(self) -> bool:
        return True

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        return {"shows": 500.0, "clicks": 25.0, "spent": 300.0, "goals": 8.0}

    async def get_status(self, campaign_id: str) -> str:
        return "blocked"


def _detached_copy(campaign: Campaign) -> Campaign:
    """Транзиентная копия строки — НЕ добавлена в сессию, вне identity-map.

    Инструмент проверки: если бы `collect_digest` читал статус напрямую из
    объекта `Campaign`, отобранного до синка (старое поведение, негласно
    полагавшееся на то, что `sync_campaign_stats` мутирует именно ЭТОТ же
    Python-объект через identity-map сессии), — он увидел бы статус ЭТОЙ
    копии, замороженный на момент отбора, и не заметил бы правку синка,
    выполненную над настоящим объектом сессии.
    """
    return Campaign(
        id=campaign.id,
        account_id=campaign.account_id,
        brief_id=campaign.brief_id,
        client_id=campaign.client_id,
        cabinet_id=campaign.cabinet_id,
        ad_account_id=campaign.ad_account_id,
        status=campaign.status,
        objective=campaign.objective,
        spec_json=campaign.spec_json,
        external_id=campaign.external_id,
    )


def test_collect_digest_reflects_status_changed_by_real_sync(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`sync_campaign_stats` НЕ подменён — идёт настоящим путём выбора адаптера
    (как в `test_stats_sync.py`), площадка отвечает `blocked` → кампания
    становится `stopped` прямо во время синка. Строка отчёта обязана показать
    `stopped`, а не `launched`, с которым кампания была ДО прогона.

    `list_active_campaigns` здесь подменена на версию, отдающую ТРАНЗИЕНТНЫЕ
    копии строк (`_detached_copy`) — вне identity-map сессии. Это делает тест
    настоящим регрессионным: реализация, которая просто читает
    `campaign.status` у объекта, отобранного до синка (старое поведение),
    здесь провалится (увидит замороженный `launched`), а `collect_digest`
    обязан перечитать статус явным запросом (`_post_sync_status` /
    `db.repositories.get_campaign`), чтобы увидеть настоящий `stopped`.
    """

    async def identity(token: str, **_: object) -> VkIdentity:
        return VkIdentity("10000001", "a1b2c3d4e5@agency_client", "Кабинет «Тест»", "active")

    async def balance(token: str, **_: object) -> str | None:
        return None

    async def detached_list_active_campaigns(
        session: AsyncSession, account_id: int
    ) -> list[Campaign]:
        campaigns = await list_active_campaigns(session, account_id)
        return [_detached_copy(c) for c in campaigns]

    monkeypatch.setattr(ad_accounts, "fetch_identity", identity)
    monkeypatch.setattr(ad_accounts, "fetch_balance", balance)
    monkeypatch.setattr(launch_service, "VkApiAdapter", _StatusFlipAdapter)
    monkeypatch.setattr(
        daily_digest_module, "list_active_campaigns", detached_list_active_campaigns
    )

    async def scenario(session: AsyncSession) -> DigestReport:
        cfg = _live_settings()
        session.add(
            Cabinet(
                id=1,
                account_id=1,
                client_id=1,
                channel="vk_api",
                ad_object_url="https://vk.com/id1",
            )
        )
        await session.commit()
        ad_account = await add_account(session, 1, TOKEN, settings=cfg)
        await session.commit()
        session.add(_campaign(1, external_id="vk-1", cabinet_id=1, ad_account_id=ad_account.id))
        await session.commit()
        return await collect_digest(session, 1, cfg)

    report = asyncio.run(_with_db(scenario))
    assert len(report.rows) == 1
    row = report.rows[0]
    assert row.status == "stopped"  # не "launched", с которым кампания была до синка
    assert row.shows == 500.0
    assert row.stale is False
