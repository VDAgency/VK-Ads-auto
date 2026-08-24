"""Сшивка «бриф + креатив → запуск рекламной кампании» (spec 2026-07-17 §8).

Триггер — загрузка креатива. Разбираем бриф (`parse_brief`), строим `CampaignSpec`
(`build_campaign_spec`), выбираем канал через `ChannelRouter` (health-check +
фолбэк), переиспользуем или заводим `Cabinet`, создаём кампанию через
`PlatformAdapter.create_campaign_from_spec` и персистим `Creative`/`Campaign`.

Предохранители (CLAUDE.md §1.4):
- `vk_live_campaigns` — боевое создание кампании через VK API; снят → `StubAdapter`;
- `vk_campaign_autostart` — автозапуск созданной кампании; снят → статус `prepared`;
- `vk_agency_confirmed` — боевое создание КАБИНЕТОВ VK. Сейчас кампании идут только
  в уже заведённый кабинет оператора, поэтому кабинеты на площадке не создаются вовсе.
Кампания на заглушке сохраняется со статусом `prepared` — боевых мутаций нет.

Отказ боевого канала посреди запуска (сервис kotbot ещё без живых флоу — 501,
`reauth_required`, сеть) не имитируем успехом: кампания переигрывается на заглушке,
оператор получает «подготовлена, но не запущена» + уведомление.
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
from integrations.kotbot_http import KotbotAdapter
from integrations.stub import StubAdapter
from integrations.vk_api import VkApiAdapter
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from services.ad_accounts import (
    AccountNotFoundError,
    AdAccountView,
    AmbiguousAdAccountError,
    NoAdAccountError,
    TokenUnavailableError,
    get_account,
    mark_unauthorized,
    resolve_default_account,
    resolve_token,
)
from services.brief_parser import BriefVariant, parse_brief
from services.launch import LaunchResult, daily_budget_rub, run_campaign
from services.mapping import CampaignSpec, UnsupportedBriefGoalError, build_campaign_spec
from services.notifier import notify_operator
from services.secret_box import NotConfiguredError

logger = logging.getLogger(__name__)

# Канал заглушки в `Cabinet.channel` (набор: kotbot | vk_api | stub).
STUB_CHANNEL = "stub"

# Статусы площадки, которые считаем модерацией (VK отдаёт `moderation`/`pending`).
# Публичная константа: тем же признаком пользуется синк статистики (`services/stats_sync`).
MODERATION_MARKERS = ("moder", "pending")


# Цели рекламы, принимаемые этим валидатором запуска. «Сообщения» прошли боевой
# зонд 2026-08-23 (integrations.vk_surfaces.VK_MESSAGES.verified=True) и в боте/вебе
# выбираются как обычная цель. «Заявка через Senler» технически работает тем же
# пакетом VK, что и «Сообщения» (integrations.vk_surfaces.VK_SENLER), но собственный
# боевой прогон под именем Senler ещё не проведён (Surface.verified=False) — в
# каталоге площадок подписки она по-прежнему показывается как «скоро»
# (services.goals.subscription_targets().available), при этом оператор уже может
# явно выбрать её при запуске (bot.handlers.creative.GOALS), и запуск её принимает.
SUBSCRIBERS_GOAL = "subscribers"
LEAD_FORM_GOAL = "lead_form"
MESSAGES_GOAL = "messages"
SENLER_GOAL = "senler"
SUPPORTED_GOALS = (SUBSCRIBERS_GOAL, LEAD_FORM_GOAL, MESSAGES_GOAL, SENLER_GOAL)


class BriefNotFoundError(Exception):
    """Брифа нет у тенанта — нельзя запустить кампанию."""


class UnsupportedGoalError(Exception):
    """Цель ещё не реализована — кампанию с ней не запускаем."""


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


def _build_adapters(settings: Settings, vk_token: SecretStr) -> dict[Channel, PlatformAdapter]:
    """Адаптеры каналов по конфигу. VK-канал без разрешения — заглушка (фолбэк).

    `vk_token` — токен рекламного кабинета (выбранного оператором или кабинета
    по умолчанию). Обязателен: токен из окружения (`VK_ADS_ACCESS_TOKEN`) для
    боевых вызовов больше не используется — только для посева (`seed_from_env`).

    Предохранитель `vk_live_campaigns` проверяется ЗДЕСЬ, до и независимо от
    выбора кабинета: живой кабинет с валидным токеном сам по себе ничего не
    разрешает (CLAUDE.md §1.4).
    """
    adapters: dict[Channel, PlatformAdapter] = {}
    if vk_token.get_secret_value() and settings.vk_live_campaigns:
        adapters[Channel.VK_API] = VkApiAdapter(vk_token)
    else:
        adapters[Channel.VK_API] = StubAdapter()
    if settings.kotbot_base_url:
        adapters[Channel.KOTBOT] = KotbotAdapter(settings.kotbot_base_url)
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


def _build_router(settings: Settings, vk_token: SecretStr) -> ChannelRouter:
    """Роутер каналов: адаптеры + конфиг выбора (health-check и фолбэк внутри)."""
    return ChannelRouter(_build_adapters(settings, vk_token), _channel_config(settings))


def _live_channel_expected(settings: Settings, vk_token: SecretStr) -> bool:
    """Ждали ли мы боевого канала (есть ли что «терять» при фолбэке на заглушку)."""
    token = vk_token.get_secret_value()
    return bool(settings.kotbot_base_url) or bool(token and settings.vk_live_campaigns)


def _channel_name(channel: Channel, adapter: PlatformAdapter) -> str:
    """Имя канала для `Cabinet.channel`: заглушка пишется как `stub`, не как VK."""
    return STUB_CHANNEL if isinstance(adapter, StubAdapter) else channel.value


async def _select_channel(
    settings: Settings, router: ChannelRouter | None, vk_token: SecretStr
) -> tuple[Channel, PlatformAdapter, bool]:
    """Выбрать канал. Третий элемент — был ли фолбэк на заглушку с боевого канала."""
    try:
        channel, adapter = await (router or _build_router(settings, vk_token)).select()
    except NoHealthyChannelError:
        logger.warning("No healthy integration channel; falling back to stub")
        return Channel.VK_API, StubAdapter(), True
    fallback = isinstance(adapter, StubAdapter) and _live_channel_expected(settings, vk_token)
    return channel, adapter, fallback


def _validate_goal(goal: str | None) -> None:
    """Отклонить цель, логики запуска для которой ещё нет.

    Молча подменять цель нельзя: оператор увидел бы «запущено» и получил не ту
    кампанию, за которую платит клиент.
    """
    if goal is not None and goal not in SUPPORTED_GOALS:
        raise UnsupportedGoalError(goal)


def _is_unauthorized(exc: BaseException) -> bool:
    """Отличить «VK отклонил токен» от прочих отказов канала.

    Смотрим на ответ httpx: 401/403 приходят из `raise_for_status()` адаптера.
    """
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    return status_code in (401, 403)


async def _resolve_ad_account(
    session: AsyncSession,
    account_id: int,
    ad_account_id: int | None,
    settings: Settings,
) -> tuple[AdAccountView, SecretStr]:
    """Выбранный кабинет и его токен.

    Отдельная функция, потому что оба значения нужны в разных местах запуска:
    токен уходит в адаптер, а сам кабинет — в `external_ref` и в `Campaign`.

    Оператор не выбрал кабинет (`ad_account_id is None`) — берём кабинет по
    умолчанию (`resolve_default_account`): единственный активный. Кабинетов нет
    или их несколько — явная ошибка (`NoAdAccountError`/`AmbiguousAdAccountError`),
    а не угадывание и не токен из окружения: `VK_ADS_ACCESS_TOKEN` для запуска
    больше не используется (только для одноразового посева, `seed_from_env`).
    """
    if ad_account_id is None:
        return await resolve_default_account(session, account_id, settings=settings)
    view = await get_account(session, account_id, ad_account_id)
    if view is None:
        raise AccountNotFoundError(str(ad_account_id))
    token = await resolve_token(session, account_id, ad_account_id, settings=settings)
    return view, token


async def _resolve_cabinet(
    session: AsyncSession,
    account_id: int,
    client_id: int | None,
    spec: CampaignSpec,
    channel_name: str,
    ad_account: AdAccountView,
) -> tuple[str, int | None]:
    """Reuse-or-create кабинет ДО кампании: (внешний ref для площадки, id строки БД).

    Кабинет ищем по четвёрке (тенант, клиент, канал, объект рекламы).

    Внешний ref берём у рекламного кабинета оператора: именно его токеном мы ходим
    в VK, значит там кампания и окажется. Кабинет всегда выбран заранее
    (`_resolve_ad_account`) — явно оператором либо как единственный активный,
    поэтому заводить кабинет на площадке отсюда больше не требуется.
    """
    if client_id is not None:
        existing = await find_cabinet(session, account_id, client_id, channel_name, spec.object_url)
        if existing is not None:
            return existing.external_ref or "", existing.id

    external_ref = ad_account.external_id
    if client_id is None:
        # Бриф без клиента (редкий случай): кабинет не персистим — FK не заполнить.
        return external_ref, None

    row = await create_cabinet_row(
        session,
        account_id,
        client_id,
        channel_name,
        spec.object_url,
        # Имя объекта рекламы бриф не даёт (только ссылку) — площадка допишет позже.
        external_ref=external_ref,
    )
    return external_ref, row.id


async def _refine_status(adapter: PlatformAdapter, external_id: str) -> str:
    """Уточнить статус у площадки: запущенная кампания часто уходит на модерацию."""
    try:
        platform_status = await adapter.get_status(external_id)
    except Exception:  # noqa: BLE001 — уточнение статуса не должно ронять запуск
        logger.exception("failed to read campaign status from platform")
        return "launched"
    lowered = platform_status.lower()
    if any(marker in lowered for marker in MODERATION_MARKERS):
        return "moderation"
    return "launched"


async def _prepare_on_platform(
    session: AsyncSession,
    account_id: int,
    client_id: int | None,
    spec: CampaignSpec,
    channel: Channel,
    adapter: PlatformAdapter,
    settings: Settings,
    *,
    creative_ref: str | None,
    title: str | None,
    body: str | None,
    ad_account: AdAccountView,
) -> tuple[int | None, LaunchResult, str]:
    """Кабинет и кампания на выбранном канале: (id кабинета, результат, статус).

    Всё общение с площадкой собрано здесь, чтобы отказ канала можно было поймать
    целиком и честно переиграть запуск на заглушке (см. `launch_from_creative`).
    """
    autostart = not isinstance(adapter, StubAdapter) and settings.vk_campaign_autostart
    cabinet_ref, cabinet_id = await _resolve_cabinet(
        session,
        account_id,
        client_id,
        spec,
        _channel_name(channel, adapter),
        ad_account,
    )
    result = await run_campaign(
        adapter,
        cabinet_ref,
        spec,
        creative_ref=creative_ref,
        title=title,
        body=body,
        budget_limit_day=daily_budget_rub(spec),
        autostart=autostart,
    )
    status = await _refine_status(adapter, result.campaign_id) if result.launched else "prepared"
    return cabinet_id, result, status


async def launch_from_creative(
    session: AsyncSession,
    account_id: int,
    brief_id: int,
    media_type: str,
    file_path: str | None,
    title: str | None,
    body: str | None,
    *,
    settings: Settings | None = None,
    router: ChannelRouter | None = None,
    ad_account_id: int | None = None,
    goal: str | None = None,
) -> LaunchOutcome:
    """Сохранить креатив, разложить бриф и создать кампанию. Коммит — на вызывающем.

    Порядок (spec §8): parse → spec → `Creative` → reuse-or-create `Cabinet`
    (персистим ДО кампании, чтобы ретрай после таймаута переиспользовал кабинет)
    → создание кампании через адаптер → `Campaign`.

    `ad_account_id` — рекламный кабинет, выбранный оператором: его токеном идёт
    обращение к VK, в нём же окажется кампания. Не передан → берём кабинет по
    умолчанию (единственный активный, `resolve_default_account`); токен из
    окружения для запуска больше не используется.

    `goal` — цель рекламы, которую явно выбрал оператор (кнопка в боте); неизвестное
    значение отклоняется, чтобы кампания не ушла с чужой целью. Отдельно от этого
    параметра цель может прийти из самого брифа (`ParsedBrief.goal`, площадка
    `target_type`) — сейчас раскладка (`services.mapping.build_campaign_spec`)
    поддерживает все четыре цели перечисления `Goal` (подписчики, лид-форма,
    сообщения, Senler); бриф с ещё не реализованной будущей целью по-прежнему
    отклоняется тем же `UnsupportedGoalError`, что и неизвестный параметр `goal`.

    Бросает `BriefNotFoundError`, если брифа нет, `BriefValidationError`
    (из `parse_brief`), `UnsupportedGoalError` (неподдержанный параметр `goal`
    ИЛИ неподдержанная цель самого брифа) и ошибки выбора кабинета
    (`AccountNotFoundError`, `TokenUnavailableError`, `NoAdAccountError`,
    `AmbiguousAdAccountError`).
    """
    cfg = settings or get_settings()
    _validate_goal(goal)
    brief = await get_brief(session, account_id, brief_id)
    if brief is None:
        raise BriefNotFoundError(str(brief_id))

    # Кабинет разбираем до брифа: его пригодность от брифа не зависит, а оператор
    # должен узнать про мёртвый доступ сразу, а не после разбора полей.
    ad_account, vk_token = await _resolve_ad_account(session, account_id, ad_account_id, cfg)

    parsed = parse_brief(brief.payload, BriefVariant(brief.variant))
    try:
        spec = build_campaign_spec(parsed)
    except UnsupportedBriefGoalError as exc:
        # Транслируем в тот же тип, что и неподдержанный параметр `goal` (`_validate_goal`
        # выше): роутерам и боту достаточно ловить один `UnsupportedGoalError`, чтобы
        # честно ответить 422 вместо утечки 500 в VK.
        raise UnsupportedGoalError(exc.goal.value) from exc

    # Продвижение готового поста обходится без креатива: объявлением служит сам пост,
    # и требовать от оператора картинку было бы выдумкой на пустом месте.
    if file_path is not None:
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

    channel, adapter, fallback = await _select_channel(cfg, router, vk_token)

    async def prepare(platform: PlatformAdapter) -> tuple[int | None, LaunchResult, str]:
        return await _prepare_on_platform(
            session,
            account_id,
            brief.client_id,
            spec,
            channel,
            platform,
            cfg,
            creative_ref=file_path,
            title=title,
            body=body,
            ad_account=ad_account,
        )

    try:
        cabinet_id, result, status = await prepare(adapter)
    except Exception as exc:  # noqa: BLE001 — любой отказ канала → честный фолбэк, не 500
        if isinstance(adapter, StubAdapter):
            # Заглушка ничего не мутирует: её падение — баг, а не отказ канала.
            raise
        # Отозванный токен — не «канал моргнул»: помечаем кабинет мёртвым сразу,
        # чтобы оператор увидел причину в списке, а не гадал по фолбэку.
        if _is_unauthorized(exc):
            await mark_unauthorized(
                session, account_id, ad_account.id, "VK отклонил токен при запуске"
            )
        # Канал ответил отказом (нет флоу/переавторизация/сеть): успех не имитируем —
        # готовим кампанию на заглушке и честно говорим оператору, что запуска не было.
        logger.exception("Channel %s failed during launch; falling back to stub", channel.value)
        adapter = StubAdapter()
        fallback = True
        cabinet_id, result, status = await prepare(adapter)

    is_live = not isinstance(adapter, StubAdapter)
    campaign = Campaign(
        account_id=account_id,
        brief_id=brief_id,
        client_id=brief.client_id,
        cabinet_id=cabinet_id,
        ad_account_id=ad_account.id,
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


def adapter_for_channel(
    settings: Settings, channel_name: str, vk_token: SecretStr
) -> PlatformAdapter:
    """Адаптер канала, которым кампания была создана (для остановки/статуса/статистики).

    `vk_token` обязателен — токен кабинета, к которому относится кампания
    (см. `campaign_vk_token`). Неизвестное имя канала (в т.ч. `stub`) → заглушка:
    боевых мутаций не делаем.
    """
    adapters = _build_adapters(settings, vk_token)
    try:
        channel = Channel(channel_name)
    except ValueError:
        return StubAdapter()
    return adapters.get(channel, StubAdapter())


async def campaign_vk_token(
    session: AsyncSession, account_id: int, campaign: Campaign, settings: Settings
) -> SecretStr | None:
    """Токен кабинета, в котором заведена кампания (для остановки и статистики).

    При нескольких кабинетах токен из окружения больше не годится: остановить
    кампанию можно только тем доступом, которым она создавалась. `None` — у
    кампаний, созданных до мультикабинетности, либо если кабинет уже удалён:
    вызывающая сторона в этом случае берёт кабинет по умолчанию
    (`resolve_default_account`), а не токен из окружения.
    """
    if campaign.ad_account_id is None:
        return None
    try:
        return await resolve_token(session, account_id, campaign.ad_account_id, settings=settings)
    except (AccountNotFoundError, TokenUnavailableError, NotConfiguredError):
        logger.warning(
            "campaign %s references ad account %s without a usable token",
            campaign.id,
            campaign.ad_account_id,
        )
        return None


async def campaign_channel(session: AsyncSession, account_id: int, campaign: Campaign) -> str:
    """Имя канала кампании (по её кабинету). Без кабинета — заглушка."""
    if campaign.cabinet_id is None:
        return STUB_CHANNEL
    cabinet = await get_cabinet(session, account_id, campaign.cabinet_id)
    return cabinet.channel if cabinet is not None else STUB_CHANNEL


async def resolve_channel_vk_token(
    session: AsyncSession,
    account_id: int,
    campaign: Campaign,
    channel_name: str,
    settings: Settings,
) -> SecretStr | None:
    """Токен для канала, которым заведена кампания. `None` — кабинет не определить.

    Порядок: токен кабинета самой кампании → для не-VK каналов пустой токен →
    кабинет по умолчанию.

    Каналам `stub` и `kotbot` токен VK не нужен: заглушка его игнорирует, а kotbot
    ходит своим доступом. Требовать ради них заведённый кабинет значило бы
    запретить останавливать и синхронизировать подготовленные кампании, пока
    оператор не добавит кабинет, — а площадку при этом никто не трогает.
    """
    token = await campaign_vk_token(session, account_id, campaign, settings)
    if token is not None:
        return token
    if channel_name != Channel.VK_API.value:
        return SecretStr("")
    try:
        _, token = await resolve_default_account(session, account_id, settings=settings)
    except (NoAdAccountError, AmbiguousAdAccountError) as exc:
        logger.warning("campaign %s has no resolvable ad account: %s", campaign.id, exc)
        return None
    return token


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
    чтобы оператор увидел настоящую причину, а не «успешно остановлено». Та же
    ошибка — у VK-кампаний без своего кабинета (созданных до мультикабинетности),
    если кабинет по умолчанию сейчас не определён однозначно (кабинетов нет или
    их несколько): имитировать успех нечем, площадку никто не спрашивал.
    """
    cfg = settings or get_settings()
    campaign = await get_campaign(session, account_id, campaign_id)
    if campaign is None:
        return None

    channel_name = await campaign_channel(session, account_id, campaign)
    if adapter is not None:
        platform = adapter
    else:
        vk_token = await resolve_channel_vk_token(session, account_id, campaign, channel_name, cfg)
        if vk_token is None:
            raise CampaignStopError(
                "cannot resolve the ad account for this campaign: "
                "add one via /cabinets or choose it explicitly"
            )
        platform = adapter_for_channel(cfg, channel_name, vk_token)
    if campaign.external_id:
        try:
            await platform.stop(campaign.external_id)
        except Exception as exc:  # noqa: BLE001 — любая ошибка канала → понятный ответ
            raise CampaignStopError(str(exc)) from exc
    return await set_campaign_status(session, account_id, campaign_id, "stopped")
