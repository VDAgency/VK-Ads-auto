"""Мини-отчёт клиента в его кабинете (B1): свои кампании и метрики, без расхода.

ТЗ 4.2 и критерий приёмки Блока 2: «клиентский мини-кабинет: профиль, мини-отчёт
по его целям и статистике (read-only)». Расход клиенту не показываем ни в каком
виде — сознательное решение проекта (клиент платит за услугу, а не за медиабюджет,
закупочную цену видеть не должен). Поэтому поля расхода в типах отчёта нет вовсе:
не «скрыто на фронте», а физически отсутствует в данных, которые уходят из ядра.

Метрики — последний срез по кампании, а не сумма истории: переиспользуем
`db.repositories.aggregate_cabinet_stats`, который уже решает эту задачу приёмом
`ROW_NUMBER() OVER (PARTITION BY campaign_id ORDER BY captured_at DESC)`
(см. `tests/test_cabinet_stats_service.py`) — VK отдаёт накопительный итог с начала
кампании, суммировать срезы нельзя.
"""

from __future__ import annotations

from dataclasses import dataclass

from db.models import Campaign
from db.repositories import aggregate_cabinet_stats, list_client_campaigns
from sqlalchemy.ext.asyncio import AsyncSession

from services.brief_parser import TargetType
from services.goals import target_title

# Статус кампании клиенту — по-русски, не сырым кодом площадки (`Campaign.status`,
# db/models.py). Формулировки согласованы с текстом кабинета (женский род — «кампания»).
_STATUS_RU: dict[str, str] = {
    "prepared": "подготовлена",
    "launched": "запущена",
    "moderation": "на модерации",
    "stopped": "остановлена",
    "failed": "ошибка запуска",
}

# Легаси-кампании без сохранённого/распознанного `object_kind` в `spec_json` —
# честный неопределённый ярлык вместо падения отчёта.
_UNKNOWN_GOAL_TITLE = "Реклама"


@dataclass(frozen=True, slots=True)
class ClientCampaignRow:
    """Одна кампания клиента для мини-отчёта. Поля расхода здесь нет и не будет."""

    external_id: str
    goal: str
    status: str
    shows: float
    clicks: float
    results: float

    @property
    def ctr(self) -> float:
        return round(self.clicks / self.shows * 100, 2) if self.shows else 0.0


@dataclass(frozen=True, slots=True)
class ClientReport:
    """Мини-отчёт клиента: его кампании с метриками (без расхода)."""

    campaigns: list[ClientCampaignRow]


def _goal_title(campaign: Campaign) -> str:
    """Русское название цели кампании по площадке брифа (`spec_json['object_kind']`).

    `object_kind` — значение `TargetType` (`services.brief_parser`), сохранённое в
    `CampaignSpec.object_kind` при запуске (`services.mapping.build_campaign_spec`).
    `services.goals.target_title` — уже проверенный единый источник русских названий
    площадок; не заводим здесь второй параллельный словарь.
    """
    kind = (campaign.spec_json or {}).get("object_kind")
    if not kind:
        return _UNKNOWN_GOAL_TITLE
    try:
        TargetType(kind)
    except ValueError:
        return _UNKNOWN_GOAL_TITLE
    return target_title(kind)


async def build_client_report(
    session: AsyncSession, account_id: int, client_id: int
) -> ClientReport:
    """Собрать мини-отчёт клиента: его кампании данного тенанта + метрики без расхода.

    Изоляция клиента и тенанта — на уровне SQL (`list_client_campaigns` скоупит и по
    `account_id`, и по `client_id`), а не постфильтром здесь.
    """
    campaigns = await list_client_campaigns(session, account_id, client_id)
    rows: list[ClientCampaignRow] = []
    for campaign in campaigns:
        external_id = campaign.external_id
        if not external_id:
            continue  # list_client_campaigns это уже отфильтровал; страховка типов
        agg = await aggregate_cabinet_stats(session, account_id, external_id)
        rows.append(
            ClientCampaignRow(
                external_id=external_id,
                goal=_goal_title(campaign),
                status=_STATUS_RU.get(campaign.status, campaign.status),
                shows=agg["shows"],
                clicks=agg["clicks"],
                results=agg["results"],
            )
        )
    return ClientReport(campaigns=rows)
