"""Сшивка «бриф + креатив → запуск рекламной кампании» (spec 2026-07-17 §8).

Триггер — загрузка креатива. Разбираем бриф (`parse_brief`), строим `CampaignSpec`
(`build_campaign_spec`), выбираем канал через `ChannelRouter` (health-check +
фолбэк), переиспользуем или заводим `Cabinet`, создаём кампанию через
`PlatformAdapter.create_campaign_from_spec` и персистим `Creative`/`Campaign`.

Предохранители (CLAUDE.md §1.4):
- `vk_live_campaigns` — боевое создание кампании через VK API; снят → `StubAdapter`;
- `vk_campaign_autostart` — автозапуск созданной кампании; снят → статус `prepared`;
- `vk_agency_confirmed` — боевое создание КАБИНЕТОВ VK; снят → кабинет только готовый
  (из брифа), на площадке ничего не заводим.
Кампания на заглушке сохраняется со статусом `prepared` — боевых мутаций нет.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from config.settings import Settings, get_settings
from db.models import Campaign, Creative
from db.repositories import (
    create_cabinet_row,
    find_cabinet,
    get_brief,
    get_cabinet,
    get_campaign,
    set_campaign_status,
)
from integrations.adapter import PlatformAdapter
from integrations.channels import Channel, ChannelConfig, ChannelRouter, NoHealthyChannelError
from integrations.kotbot_playwright import KotbotAdapter
from integrations.stub import StubAdapter
from integrations.vk_api import VkApiAdapter
from sqlalchemy.ext.asyncio import AsyncSession

from services.brief_parser import BriefVariant, ParsedBrief, parse_brief
from services.launch import daily_budget_rub, run_campaign
from services.mapping import CampaignSpec, build_campaign_spec
from services.notifier import notify_operator

logger = logging.getLogger(__name__)

# Канал заглушки в `Cabinet.channel` (набор: kotbot | vk_api | stub).
STUB_CHANNEL = "stub"

# Статусы площадки, которые считаем модерацией (VK отдаёт `moderation`/`pending`).
_MODERATION_MARKERS = ("moder", "pending")


class BriefNotFoundError(Exception):
    """Брифа нет у тенанта — нельзя запустить кампанию."""


class CampaignStopError(Exception):
    """Площадка не смогла остановить кампанию (сеть/недоступный канал)."""


@dataclass(frozen=True, slots=True)
class LaunchOutcome:
    """Итог подготовки/запуска кампании для оператора."""

    campaign_status: str  # prepared | launched | moderation
    campaign_id: int
    message: str


_PREPARED_MSG = (
    "🚀 Креатив принят, кампания подготовлена по брифу.\n"
    "Боевой запуск в VK включится после подтверждения агентского статуса ИП "
    "и доступа к VK API."
)
_LAUNCHED_MSG = "🚀 Креатив принят, кампания запущена в VK и добавлена в отслеживание."
_CREATED_NOT_STARTED_MSG = (
    "🚀 Креатив принят, кампания создана на площадке, но НЕ запущена: "
    "автозапуск выключен (VK_CAMPAIGN_AUTOSTART).\n"
    "Проверьте кампанию глазами и запустите вручную — деньги пока не тратятся."
)
_FALLBACK_MSG = (
    "⚠️ Боевой канал недоступен — кампания подготовлена, но не запущена.\n"
    "Проверьте канал (VK API / kotbot) и повторите загрузку креатива."
)


def _build_adapters(settings: Settings) -> dict[Channel, PlatformAdapter]:
    """Адаптеры каналов по конфигу. VK-канал без разрешения — заглушка (фолбэк)."""
    token = settings.vk_ads_access_token.get_secret_value()
    adapters: dict[Channel, PlatformAdapter] = {}
    if token and settings.vk_live_campaigns:
        adapters[Channel.VK_API] = VkApiAdapter(settings.vk_ads_access_token)
    else:
        adapters[Channel.VK_API] = StubAdapter()
    if settings.kotbot_base_url:
        adapters[Channel.KOTBOT] = KotbotAdapter()
    return adapters


def _channel_config(settings: Settings) -> ChannelConfig:
    """Канал по умолчанию + ручной переключатель (`integration_forced_channel`)."""
    default = Channel.KOTBOT if settings.kotbot_base_url else Channel.VK_API
    forced: Channel | None = None
    raw = settings.integration_forced_channel.strip()
    if raw:
        try:
            forced = Channel(raw)
        except ValueError:
            logger.warning("Unknown INTEGRATION_FORCED_CHANNEL %r ignored", raw)
    return ChannelConfig(default=default, forced=forced)


def _build_router(settings: Settings) -> ChannelRouter:
    """Роутер каналов: адаптеры + конфиг выбора (health-check и фолбэк внутри)."""
    return ChannelRouter(_build_adapters(settings), _channel_config(settings))


def _live_channel_expected(settings: Settings) -> bool:
    """Ждали ли мы боевого канала (есть ли что «терять» при фолбэке на заглушку)."""
    token = settings.vk_ads_access_token.get_secret_value()
    return bool(settings.kotbot_base_url) or bool(token and settings.vk_live_campaigns)


def _channel_name(channel: Channel, adapter: PlatformAdapter) -> str:
    """Имя канала для `Cabinet.channel`: заглушка пишется как `stub`, не как VK."""
    return STUB_CHANNEL if isinstance(adapter, StubAdapter) else channel.value


async def _select_channel(
    settings: Settings, router: ChannelRouter | None
) -> tuple[Channel, PlatformAdapter, bool]:
    """Выбрать канал. Третий элемент — был ли фолбэк на заглушку с боевого канала."""
    try:
        channel, adapter = await (router or _build_router(settings)).select()
    except NoHealthyChannelError:
        logger.warning("No healthy integration channel; falling back to stub")
        return Channel.VK_API, StubAdapter(), True
    fallback = isinstance(adapter, StubAdapter) and _live_channel_expected(settings)
    return channel, adapter, fallback


async def _resolve_cabinet(
    session: AsyncSession,
    account_id: int,
    client_id: int | None,
    spec: CampaignSpec,
    parsed: ParsedBrief,
    adapter: PlatformAdapter,
    channel_name: str,
    settings: Settings,
) -> tuple[str, int | None]:
    """Reuse-or-create кабинет ДО кампании: (внешний ref для площадки, id строки БД).

    Кабинет ищем по четвёрке (тенант, клиент, канал, объект рекламы). Если бриф
    принёс готовый `vk_ad_cabinet_id` — используем его и НИЧЕГО не создаём.
    Боевое создание кабинета VK разрешает только `vk_agency_confirmed`.
    """
    if client_id is not None:
        existing = await find_cabinet(session, account_id, client_id, channel_name, spec.object_url)
        if existing is not None:
            return existing.external_ref or "", existing.id

    external_ref = parsed.vk_ad_cabinet_id
    if external_ref is None:
        if isinstance(adapter, VkApiAdapter) and not settings.vk_agency_confirmed:
            # Агентский статус ИП не подтверждён — кабинеты в VK не заводим (§1.4).
            logger.warning("VK cabinet creation is blocked: agency status is not confirmed")
        else:
            external_ref = await adapter.create_cabinet(account_id, str(client_id or account_id))

    if client_id is None:
        # Бриф без клиента (редкий случай): кабинет не персистим — FK не заполнить.
        return external_ref or "", None

    row = await create_cabinet_row(
        session,
        account_id,
        client_id,
        channel_name,
        spec.object_url,
        # Имя объекта рекламы бриф не даёт (только ссылку) — площадка допишет позже.
        external_ref=external_ref,
    )
    return external_ref or "", row.id


async def _refine_status(adapter: PlatformAdapter, external_id: str) -> str:
    """Уточнить статус у площадки: запущенная кампания часто уходит на модерацию."""
    try:
        platform_status = await adapter.get_status(external_id)
    except Exception:  # noqa: BLE001 — уточнение статуса не должно ронять запуск
        logger.exception("failed to read campaign status from platform")
        return "launched"
    lowered = platform_status.lower()
    if any(marker in lowered for marker in _MODERATION_MARKERS):
        return "moderation"
    return "launched"


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
    router: ChannelRouter | None = None,
) -> LaunchOutcome:
    """Сохранить креатив, разложить бриф и создать кампанию. Коммит — на вызывающем.

    Порядок (spec §8): parse → spec → `Creative` → reuse-or-create `Cabinet`
    (персистим ДО кампании, чтобы ретрай после таймаута переиспользовал кабинет)
    → создание кампании через адаптер → `Campaign`.

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

    channel, adapter, fallback = await _select_channel(cfg, router)
    is_live = not isinstance(adapter, StubAdapter)
    autostart = is_live and cfg.vk_campaign_autostart

    cabinet_ref, cabinet_id = await _resolve_cabinet(
        session,
        account_id,
        brief.client_id,
        spec,
        parsed,
        adapter,
        _channel_name(channel, adapter),
        cfg,
    )

    result = await run_campaign(
        adapter,
        cabinet_ref,
        spec,
        creative_ref=file_path,
        title=title,
        body=body,
        budget_limit_day=daily_budget_rub(spec),
        autostart=autostart,
    )

    status = await _refine_status(adapter, result.campaign_id) if result.launched else "prepared"
    campaign = Campaign(
        account_id=account_id,
        brief_id=brief_id,
        client_id=brief.client_id,
        cabinet_id=cabinet_id,
        status=status,
        objective=spec.objective,
        spec_json=asdict(spec),
        external_id=result.campaign_id,
        launched_at=datetime.now(UTC) if result.launched else None,
    )
    session.add(campaign)
    await session.flush()

    message = _outcome_message(status, is_live=is_live, fallback=fallback)
    if fallback:
        # Честная обратная связь: успех не имитируем (CLAUDE.md §7).
        await notify_operator(message)
    return LaunchOutcome(campaign_status=status, campaign_id=campaign.id, message=message)


def _outcome_message(status: str, *, is_live: bool, fallback: bool) -> str:
    """Сообщение оператору по факту: запущено / создано без запуска / только подготовлено."""
    if fallback:
        return _FALLBACK_MSG
    if status in ("launched", "moderation"):
        return _LAUNCHED_MSG
    if is_live:
        return _CREATED_NOT_STARTED_MSG
    return _PREPARED_MSG


def _adapter_for_channel(settings: Settings, channel_name: str) -> PlatformAdapter:
    """Адаптер канала, которым кампания была создана (для остановки/статуса)."""
    adapters = _build_adapters(settings)
    try:
        channel = Channel(channel_name)
    except ValueError:
        return StubAdapter()
    return adapters.get(channel, StubAdapter())


async def stop_campaign(
    session: AsyncSession,
    account_id: int,
    campaign_id: int,
    *,
    settings: Settings | None = None,
    adapter: PlatformAdapter | None = None,
) -> Campaign | None:
    """Остановить кампанию на площадке и перевести её в статус `stopped`.

    `None` — кампании нет либо она принадлежит чужому тенанту (скоуп §1.3).
    Коммит — на вызывающем. Ошибку площадки поднимаем как `CampaignStopError`,
    чтобы оператор увидел настоящую причину, а не «успешно остановлено».
    """
    cfg = settings or get_settings()
    campaign = await get_campaign(session, account_id, campaign_id)
    if campaign is None:
        return None

    channel_name = STUB_CHANNEL
    if campaign.cabinet_id is not None:
        cabinet = await get_cabinet(session, account_id, campaign.cabinet_id)
        if cabinet is not None:
            channel_name = cabinet.channel

    platform = adapter or _adapter_for_channel(cfg, channel_name)
    if campaign.external_id:
        try:
            await platform.stop(campaign.external_id)
        except Exception as exc:  # noqa: BLE001 — любая ошибка канала → понятный ответ
            raise CampaignStopError(str(exc)) from exc
    return await set_campaign_status(session, account_id, campaign_id, "stopped")
