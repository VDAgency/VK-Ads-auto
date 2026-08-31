import asyncio

from integrations.adapter import PlatformAdapter
from services.launch import (
    DEFAULT_TERM_DAYS,
    LaunchResult,
    balance_below_daily_budget,
    daily_budget_rub,
    daily_budget_rub_from_amount,
    launch_confirmation,
    run_campaign,
)
from services.mapping import CampaignSpec


class _RecordingAdapter(PlatformAdapter):
    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return "cab"

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        self.calls.append(("create_campaign", cabinet_id, goal))
        return "camp-1"

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        self.calls.append(("upload_creative", campaign_id, creative_ref))
        return "crt-1"

    async def launch(self, campaign_id: str) -> None:
        self.calls.append(("launch", campaign_id))

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        return {}

    async def health_check(self) -> bool:
        return True


SPEC = CampaignSpec(objective="socialengagement", name="n", object_url="u", geo_raw="Москва")


def test_run_campaign_creates_and_launches() -> None:
    adapter = _RecordingAdapter()
    result = asyncio.run(run_campaign(adapter, "cab-1", SPEC))
    assert result.campaign_id == "camp-1"
    assert result.launched is True
    assert ("create_campaign", "cab-1", "socialengagement") in adapter.calls
    assert ("launch", "camp-1") in adapter.calls
    assert not any(call[0] == "upload_creative" for call in adapter.calls)


def test_run_campaign_uploads_creative_when_provided() -> None:
    adapter = _RecordingAdapter()
    asyncio.run(run_campaign(adapter, "cab-1", SPEC, creative_ref="ad.png"))
    assert ("upload_creative", "camp-1", "ad.png") in adapter.calls


def test_run_campaign_without_autostart_does_not_launch() -> None:
    adapter = _RecordingAdapter()
    result = asyncio.run(run_campaign(adapter, "cab-1", SPEC, autostart=False))
    assert result.launched is False
    assert not any(call[0] == "launch" for call in adapter.calls)


def test_confirmation_mentions_campaign_id() -> None:
    message = launch_confirmation(LaunchResult(campaign_id="camp-9", launched=True))
    assert "camp-9" in message


def test_daily_budget_splits_brief_amount_over_term() -> None:
    spec = CampaignSpec(
        objective="socialengagement", name="n", object_url="u", geo_raw="Москва", budget_rub=30000
    )
    assert daily_budget_rub(spec) == 1000.0


def test_daily_budget_is_none_when_budget_needs_discussion() -> None:
    spec = CampaignSpec(
        objective="socialengagement",
        name="n",
        object_url="u",
        geo_raw="Москва",
        needs_budget_discussion=True,
    )
    assert daily_budget_rub(spec) is None


def test_daily_budget_is_none_without_amount() -> None:
    assert daily_budget_rub(SPEC) is None


# --- daily_budget_rub_from_amount: формула без сборки CampaignSpec ---------------
#
# Ревью плана 2026-08-25-agency-cabinets (C1/C2): формула дневного бюджета жила в
# двух местах — здесь и копией в `bot/handlers/creative.py`. Теперь она одна,
# `daily_budget_rub` выше — тонкая обёртка поверх неё; эти тесты проверяют саму
# формулу и то, что обёртка её не подменяет собственной копией.


def test_daily_budget_rub_from_amount_splits_over_term() -> None:
    assert daily_budget_rub_from_amount(30000, False) == 1000.0


def test_daily_budget_rub_from_amount_none_when_discussion() -> None:
    assert daily_budget_rub_from_amount(30000, True) is None


def test_daily_budget_rub_from_amount_none_without_amount() -> None:
    assert daily_budget_rub_from_amount(None, False) is None


def test_daily_budget_rub_from_amount_none_for_zero_amount() -> None:
    assert daily_budget_rub_from_amount(0, False) is None


def test_daily_budget_rub_delegates_to_the_shared_formula() -> None:
    """`daily_budget_rub(spec)` не считает по-своему — зовёт
    `daily_budget_rub_from_amount` с полями спеки. Меняется знаменатель или
    округление в общей формуле — обе точки входа меняются вместе, а не расходятся."""
    spec = CampaignSpec(
        objective="socialengagement", name="n", object_url="u", geo_raw="Москва", budget_rub=12345
    )
    assert daily_budget_rub(spec) == daily_budget_rub_from_amount(12345, False)
    assert daily_budget_rub(spec) == round(12345 / DEFAULT_TERM_DAYS, 2)


# --- balance_below_daily_budget: предупреждение о балансе кабинета (C2) ----------
#
# Перенос из `bot/handlers/creative.py` (`_balance_line`) — сравнение баланса с
# дневным бюджетом решало каждое сообщение бота само; теперь решение отдаётся
# обоим каналам одной функцией.


def test_balance_below_daily_budget_true_when_balance_is_lower() -> None:
    assert balance_below_daily_budget(100.0, 30000, False) is True  # дневной = 1000.0


def test_balance_below_daily_budget_false_when_balance_is_enough() -> None:
    assert balance_below_daily_budget(5000.0, 30000, False) is False


def test_balance_below_daily_budget_false_when_budget_needs_discussion() -> None:
    """Бюджет «готов обсудить» — сравнивать не с чем, предупреждение не выдумываем."""
    assert balance_below_daily_budget(1.0, 30000, True) is False


def test_balance_below_daily_budget_false_without_amount() -> None:
    assert balance_below_daily_budget(1.0, None, False) is False
