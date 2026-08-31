"""Оркестрация запуска кампании через `PlatformAdapter`.

Ядро не знает про площадку и про её внутреннюю иерархию (у VK — ad_plan →
campaigns → banners): оно отдаёт адаптеру всю спеку одним вызовом
`create_campaign_from_spec` и, если разрешён автозапуск, просит запустить.
Реальные мутации идут только с боевым адаптером и при снятых предохранителях.
"""

from __future__ import annotations

from dataclasses import dataclass

from integrations.adapter import PlatformAdapter

from services.mapping import CampaignSpec

# Бриф задаёт бюджет на срок кампании; MVP-срок — месяц. Дневной лимит нужен
# площадке (у VK бюджет живёт на уровне вложенной кампании, не плана).
DEFAULT_TERM_DAYS = 30


@dataclass(frozen=True)
class LaunchResult:
    """Результат запуска: id кампании и факт запуска."""

    campaign_id: str
    launched: bool


def daily_budget_rub_from_amount(amount: int | None, needs_discussion: bool) -> float | None:
    """Дневной лимит бюджета по уже разобранной сумме (план
    2026-08-25-agency-cabinets, ревью C1/C2 бота): та же формула, что и
    `daily_budget_rub` ниже, но без сборки полной `CampaignSpec` — вызывающей
    стороне (карточка подтверждения запуска в боте, где бюджет уже разобран
    `services.brief_parser.parse_budget`) незачем тащить весь маппинг брифа
    ради одного числа. `daily_budget_rub` — тонкая обёртка поверх этой функции,
    так что формула теперь ровно одна: разойдись бот и реальный запуск раньше
    считали бы дневной бюджет каждый по-своему, и предупреждение о нехватке
    баланса могло бы не совпасть с тем, что площадка реально спишет.

    `None` — сумма не задана или ещё обсуждается: тогда сравнивать не с чем.
    """
    if needs_discussion or not amount:
        return None
    return round(amount / DEFAULT_TERM_DAYS, 2)


def balance_below_daily_budget(
    balance_rub: float, amount: int | None, needs_discussion: bool
) -> bool:
    """Баланс кабинета меньше дневного бюджета брифа (C2, перенос из
    `bot/handlers/creative.py:_balance_line`) — не блокирует запуск, только
    предупреждает, решение остаётся за оператором.

    `amount`/`needs_discussion` — уже разобранный бюджет брифа
    (`services.brief_parser.parse_budget`); сама формула дневного лимита —
    одна на весь проект, `daily_budget_rub_from_amount` выше. `False` — бюджет
    не задан или ещё обсуждается: сравнивать не с чем.
    """
    daily_budget = daily_budget_rub_from_amount(amount, needs_discussion)
    return daily_budget is not None and balance_rub < daily_budget


def daily_budget_rub(spec: CampaignSpec) -> float | None:
    """Дневной лимит бюджета из спеки. `None` — бюджет не задан или обсуждается.

    `None` означает «ключ не отправляем площадке», а не «ноль»: без бюджета
    кампания создаётся с настройками кабинета по умолчанию.
    """
    return daily_budget_rub_from_amount(spec.budget_rub, spec.needs_budget_discussion)


async def run_campaign(
    adapter: PlatformAdapter,
    cabinet_id: str,
    spec: CampaignSpec,
    creative_ref: str | None = None,
    *,
    title: str | None = None,
    body: str | None = None,
    budget_limit_day: float | None = None,
    autostart: bool = True,
) -> LaunchResult:
    """Создать кампанию по спеке и, если разрешён автозапуск, запустить её."""
    campaign_id = await adapter.create_campaign_from_spec(
        cabinet_id,
        spec,
        creative_ref=creative_ref,
        title=title,
        body=body,
        budget_limit_day=budget_limit_day,
        # Безопасность: VK создаёт кампанию активной, поэтому решение о трате
        # денег передаём прямо в создание, а не отдельным шагом после него.
        activate=autostart,
    )
    if not autostart:
        return LaunchResult(campaign_id=campaign_id, launched=False)
    await adapter.launch(campaign_id)
    return LaunchResult(campaign_id=campaign_id, launched=True)


def launch_confirmation(result: LaunchResult) -> str:
    """Сообщение оператору после запуска (Сценарий A, шаг 9)."""
    return f"Кабинет создан, кампания запущена (id {result.campaign_id}), добавлено в отслеживание."
