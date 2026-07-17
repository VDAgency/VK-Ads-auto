"""Сшивка «бриф + креатив → запуск рекламной кампании» (spec 2026-07-17 §5.3/§6).

Триггер — загрузка креатива. Разбираем бриф (`parse_brief`), строим `CampaignSpec`
(`build_campaign_spec`), выбираем адаптер и запускаем через `run_campaign`, затем
персистим `Creative` и `Campaign`.

Боевой `VkApiAdapter` включается только при наличии токена И подтверждённом
агентском статусе ИП (CLAUDE.md §1.4); иначе — `StubAdapter`, кампания
сохраняется со статусом `prepared` (боевых мутаций VK нет).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from config.settings import Settings, get_settings
from db.models import Campaign, Creative
from db.repositories import get_brief
from integrations.adapter import PlatformAdapter
from integrations.stub import StubAdapter
from integrations.vk_api import VkApiAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from services.brief_parser import BriefVariant, parse_brief
from services.launch import run_campaign
from services.mapping import build_campaign_spec


class BriefNotFoundError(Exception):
    """Брифа нет у тенанта — нельзя запустить кампанию."""


@dataclass(frozen=True, slots=True)
class LaunchOutcome:
    """Итог подготовки/запуска кампании для оператора."""

    campaign_status: str  # prepared | launched
    campaign_id: int
    message: str


_PREPARED_MSG = (
    "🚀 Креатив принят, кампания подготовлена по брифу.\n"
    "Боевой запуск в VK включится после подтверждения агентского статуса ИП "
    "и доступа к VK API."
)
_LAUNCHED_MSG = "🚀 Креатив принят, кампания запущена в VK и добавлена в отслеживание."


def _select_adapter(settings: Settings) -> tuple[PlatformAdapter, str]:
    """Адаптер + целевой статус кампании: боевой VK → launched, заглушка → prepared."""
    token = settings.vk_ads_access_token.get_secret_value()
    if token and settings.vk_agency_confirmed:
        return VkApiAdapter(settings.vk_ads_access_token), "launched"
    return StubAdapter(), "prepared"


async def launch_from_creative(
    session: AsyncSession,
    account_id: int,
    brief_id: int,
    media_type: str,
    file_path: str,
    title: str | None,
    body: str | None,
    *,
    settings: Settings | None = None,
) -> LaunchOutcome:
    """Сохранить креатив, разложить бриф и создать кампанию. Коммит — на вызывающем.

    Бросает `BriefNotFoundError`, если брифа нет, и `BriefValidationError`
    (из `parse_brief`), если брифу не хватает обязательных полей.
    """
    cfg = settings or get_settings()
    brief = await get_brief(session, account_id, brief_id)
    if brief is None:
        raise BriefNotFoundError(str(brief_id))

    parsed = parse_brief(brief.payload, BriefVariant(brief.variant))
    spec = build_campaign_spec(parsed)

    session.add(
        Creative(
            account_id=account_id,
            brief_id=brief_id,
            media_type=media_type,
            file_path=file_path,
            title=title,
            body=body,
        )
    )

    adapter, status = _select_adapter(cfg)
    cabinet_id = await adapter.create_cabinet(account_id, str(brief.client_id))
    result = await run_campaign(adapter, cabinet_id, spec, creative_ref=file_path)

    campaign = Campaign(
        account_id=account_id,
        brief_id=brief_id,
        client_id=brief.client_id,
        status=status,
        objective=spec.objective,
        spec_json=asdict(spec),
        external_id=result.campaign_id,
        launched_at=datetime.now(UTC) if status == "launched" else None,
    )
    session.add(campaign)
    await session.flush()

    message = _LAUNCHED_MSG if status == "launched" else _PREPARED_MSG
    return LaunchOutcome(campaign_status=status, campaign_id=campaign.id, message=message)
