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


def daily_budget_rub(spec: CampaignSpec) -> float | None:
    """Дневной лимит бюджета из спеки. `None` — бюджет не задан или обсуждается.

    `None` означает «ключ не отправляем площадке», а не «ноль»: без бюджета
    кампания создаётся с настройками кабинета по умолчанию.
    """
    if spec.needs_budget_discussion or not spec.budget_rub:
        return None
    return round(spec.budget_rub / DEFAULT_TERM_DAYS, 2)


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
