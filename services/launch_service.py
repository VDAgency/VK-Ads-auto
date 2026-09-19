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
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from urllib.parse import urlsplit

from config.settings import Settings, get_settings
from db.community_tokens import find_decrypted_token
from db.models import Campaign, Creative
from db.repositories import (
    create_cabinet_row,
    find_cabinet,
    get_brief,
    get_cabinet,
    get_campaign,
    list_campaigns_for_brief,
    lock_brief_for_launch,
    set_campaign_status,
)
from integrations.adapter import PlatformAdapter
from integrations.channels import Channel, ChannelConfig, ChannelRouter, NoHealthyChannelError
from integrations.kotbot_http import KotbotAdapter
from integrations.stub import StubAdapter
from integrations.vk_api import VkApiAdapter
from integrations.vk_community import VkCommunityUnreachable, fetch_callback_servers
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
from services.brief_parser import BriefVariant, Goal, parse_brief, parse_budget
from services.brief_view import field_value, get_brief_card, tax_id
from services.goals import launch_goal_title
from services.launch import (
    LaunchResult,
    balance_below_daily_budget,
    daily_budget_rub,
    daily_budget_rub_from_amount,
    run_campaign,
)
from services.mapping import CampaignSpec, UnsupportedBriefGoalError, build_campaign_spec
from services.notifier import notify_operator
from services.secret_box import NotConfiguredError
from services.senler import detect_senler

logger = logging.getLogger(__name__)

# Канал заглушки в `Cabinet.channel` (набор: kotbot | vk_api | stub).
STUB_CHANNEL = "stub"

# Статусы площадки, которые считаем модерацией (VK отдаёт `moderation`/`pending`).
# Публичная константа: тем же признаком пользуется синк статистики (`services/stats_sync`).
MODERATION_MARKERS = ("moder", "pending")

# Статусы кампании, при которых повторный запуск по тому же брифу считается
# опасным дублем (spec 2026-09-19-block1-remaining-gaps §F): кампания ещё не
# завершена, деньги клиента либо уже тратятся, либо вот-вот начнут. `stopped` и
# `failed` сюда не попадают намеренно — остановленную или неудавшуюся кампанию
# запускать заново нужно без лишних подтверждений.
ACTIVE_CAMPAIGN_STATUSES = ("prepared", "launched", "moderation")


# Цели рекламы, принимаемые этим валидатором запуска. «Сообщения» прошли боевой
# зонд 2026-08-23 (integrations.vk_surfaces.VK_MESSAGES.verified=True) и в боте/вебе
# выбираются как обычная цель. «Заявка через Senler» технически работает тем же
# пакетом VK, что и «Сообщения» (integrations.vk_surfaces.VK_SENLER), и собственный
# боевой прогон под именем Senler тоже проведён 2026-08-24 (Surface.verified=True) —
# в каталоге площадок подписки она теперь показывается как доступная
# (services.goals.subscription_targets().available), и оператор явно выбирает её
# при запуске (bot.handlers.creative.GOALS) так же, как остальные цели.
SUBSCRIBERS_GOAL = "subscribers"
LEAD_FORM_GOAL = "lead_form"
MESSAGES_GOAL = "messages"
SENLER_GOAL = "senler"
SUPPORTED_GOALS = (SUBSCRIBERS_GOAL, LEAD_FORM_GOAL, MESSAGES_GOAL, SENLER_GOAL)

# Числовой адрес сообщества внутри ссылки (`club228817082`, `public228817082`,
# реже `event…`/`id…`) — используется ТОЛЬКО проверкой подключения Senler
# (`_community_reference`), не связан с `integrations.vk_api._COMMUNITY_RE`.
_COMMUNITY_SLUG_RE = re.compile(r"^(?:club|public|event|id)(\d+)$")


class BriefNotFoundError(Exception):
    """Брифа нет у тенанта — нельзя запустить кампанию."""


class UnsupportedGoalError(Exception):
    """Цель ещё не реализована — кампанию с ней не запускаем."""


class SenlerNotConnectedError(Exception):
    """К сообществу привязан токен, но чат-бот Senler к нему не подключён.

    Кампания в этом случае НЕ создаётся: заявки уходили бы в пустоту, а
    оператор увидел бы «запущено» и узнал правду только от клиента.
    """

    def __init__(self, community_id: str) -> None:
        super().__init__(community_id)
        self.community_id = community_id


class AdAccountClientMismatchError(Exception):
    """Кабинет закреплён за другим клиентом — этому брифу он недоступен.

    Жёсткий отказ (spec 2026-08-25-cabinet-client-binding-design §1.2): деньги
    списались бы с чужого бюджета, а реклама ушла бы от чужого имени. Общий
    кабинет (`ad_account.client_id is None`) сюда не попадает — он подходит
    любому брифу (обратная совместимость).
    """

    def __init__(
        self, ad_account_id: int, ad_account_client_id: int, brief_client_id: int | None
    ) -> None:
        super().__init__(
            f"ad account {ad_account_id} is bound to client {ad_account_client_id}, "
            f"brief belongs to client {brief_client_id!r}"
        )
        self.ad_account_id = ad_account_id
        self.ad_account_client_id = ad_account_client_id
        self.brief_client_id = brief_client_id


class AdvertiserMismatchError(Exception):
    """ИНН конечного рекламодателя кабинета и брифа различаются.

    Жёсткий отказ (spec §1.3): именно это просил не допускать заказчик —
    «конечный рекламодатель должен соответствовать брифу». Срабатывает только
    когда ИНН известен с обеих сторон; отсутствие ИНН — не признак ошибки.
    """

    def __init__(self, ad_account_id: int, ad_account_inn: str, brief_tax_id: str) -> None:
        super().__init__(
            f"ad account {ad_account_id} advertiser INN {ad_account_inn!r} "
            f"!= brief tax_id {brief_tax_id!r}"
        )
        self.ad_account_id = ad_account_id
        self.ad_account_inn = ad_account_inn
        self.brief_tax_id = brief_tax_id


class CampaignAlreadyExistsError(Exception):
    """По этому брифу уже есть кампания в незавершённом статусе на боевом канале
    (spec 2026-09-19-block1-remaining-gaps §F).

    Защита от двойной траты бюджета клиента: без неё второй запуск по тому же
    брифу (из другой вкладки, из бота параллельно с вебом или прямым вызовом API)
    молча создавал бы вторую кампанию — раньше это перехватывал только веб-мастер
    (commit 2969d33), а бот и прямой API оставались беззащитны. Кампании
    заглушки-фолбэка (`_is_stub_campaign`) не считаются: иначе после честного
    отказа боевого канала повторить запуск было бы вообще нельзя. Снимается
    явным `allow_relaunch=True` — оператор мог решить запустить вторую кампанию
    осознанно (веб уже даёт для этого отдельное подтверждённое действие).
    """

    def __init__(self, campaign_id: int, status: str) -> None:
        super().__init__(f"active campaign {campaign_id} already exists with status {status!r}")
        self.campaign_id = campaign_id
        self.status = status


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
_SENLER_UNVERIFIED_NOTE = (
    "⚠️ Подключение Senler к сообществу проверить не удалось — сверьте вручную, "
    "что чат-бот отвечает на сообщения."
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


def _digits_only(value: str) -> str:
    """ИНН без пробелов и прочих разделителей — оператор мог ввести его как удобно."""
    return re.sub(r"\D+", "", value)


def ad_account_client_mismatch(ad_account: AdAccountView, brief_client_id: int | None) -> bool:
    """Кабинет закреплён за ДРУГИМ клиентом, чем бриф — деньги спишутся не с того
    счёта (spec 2026-08-25 §1.2). Общий кабинет (`client_id is None`) подходит
    любому брифу — не признак ошибки.

    Читающая версия первого правила `_check_ad_account_matches_brief` ниже (не
    бросает исключение) — нужна карточке предпросмотра запуска (`launch_preview`),
    которая показывает оператору то же условие, что реальный запуск отклонит
    `AdAccountClientMismatchError`, но без побочных эффектов.
    """
    return ad_account.client_id is not None and ad_account.client_id != brief_client_id


def _check_ad_account_matches_brief(
    ad_account: AdAccountView, brief_client_id: int | None, brief_tax_id: str | None
) -> None:
    """Сверить выбранный кабинет с брифом (spec 2026-08-25 §1.2-1.3). Два жёстких правила:

    1. у кабинета указан клиент, и он не совпадает с клиентом брифа — отказ
       (`AdAccountClientMismatchError`), в том числе если у брифа клиента нет вовсе;
    2. у кабинета и у брифа известен ИНН конечного рекламодателя, и они различаются
       по цифрам — отказ (`AdvertiserMismatchError`).

    Отсутствие данных — не повод отказывать: общий кабинет (`client_id is None`)
    подходит любому брифу, а ИНН неизвестен хотя бы с одной стороны у большинства
    брифов физлиц. Молчаливое сравнение по имени рекламодателя НЕ делаем — там
    опечатки и сокращения, ложный отказ гарантирован.
    """
    if ad_account_client_mismatch(ad_account, brief_client_id):
        # `ad_account_client_mismatch` истинно только когда `client_id` не пуст —
        # но mypy об этом не знает через границу вызова функции, отсюда `assert`.
        assert ad_account.client_id is not None
        raise AdAccountClientMismatchError(ad_account.id, ad_account.client_id, brief_client_id)

    account_inn = ad_account.advertiser_inn
    if not account_inn or not brief_tax_id:
        return
    account_digits = _digits_only(account_inn)
    brief_digits = _digits_only(brief_tax_id)
    if account_digits and brief_digits and account_digits != brief_digits:
        raise AdvertiserMismatchError(ad_account.id, account_inn, brief_tax_id)


def _is_stub_campaign(campaign: Campaign) -> bool:
    """Кампания создана на заглушке (`StubAdapter`), а не на боевом канале.

    Признак — внешний id: `StubAdapter.create_campaign` (`integrations/stub.py`)
    всегда отдаёт `f"stub-campaign-{cabinet_id}"`, и дефолтная реализация
    `PlatformAdapter.create_campaign_from_spec` (`integrations/adapter.py`), которой
    заглушка пользуется, этот формат не меняет. Строится именно на этом, а не на
    `Cabinet.channel == "stub"`: кабинет для брифов без клиента вообще не
    персистится (`_resolve_cabinet` ниже, `client_id is None` — редкий, но
    возможный край), и тогда `cabinet_id` был бы `None` независимо от того, боевой
    канал был или заглушка — признак по кабинету в этом крае молчал бы неверно.
    """
    return campaign.external_id is not None and campaign.external_id.startswith("stub-campaign-")


async def _check_no_active_campaign(
    session: AsyncSession, account_id: int, brief_id: int, *, allow_relaunch: bool
) -> None:
    """Отказать повторному запуску по брифу, если по нему уже есть незавершённая
    кампания на боевом канале (spec §F). `allow_relaunch=True` — оператор явно
    подтвердил повторный запуск (веб уже это умеет, commit 2969d33; бот и прямой
    API теперь спрашивают то же самое, см. `CampaignAlreadyExistsError`) — тогда
    проверка снимается совсем.

    Смотрим ВСЕ кампании брифа (`list_campaigns_for_brief`), не только последнюю:
    честный фолбэк на заглушку (`launch_from_creative` ниже) оставляет по строке
    на каждый запуск, и предыдущая попытка вполне может быть заглушкой, а более
    ранняя — настоящей незавершённой кампанией.
    """
    if allow_relaunch:
        return
    campaigns = await list_campaigns_for_brief(session, account_id, brief_id)
    for campaign in campaigns:
        if campaign.status in ACTIVE_CAMPAIGN_STATUSES and not _is_stub_campaign(campaign):
            raise CampaignAlreadyExistsError(campaign.id, campaign.status)


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


@dataclass(frozen=True, slots=True)
class LaunchPreview:
    """Карточка предпросмотра запуска без креатива — то же самое, что видит
    оператор бота на карточке подтверждения перед отправкой
    (`bot/handlers/creative.py::render_launch_confirmation`), полями, а не
    готовым текстом: рендер — дело канала (CLAUDE.md §1.3). Веб-мастеру запуска
    она нужна затем же, зачем боту: запуск должен идти только после явного
    подтверждения оператором, а не сразу по кнопке.
    """

    client_name: str | None
    client_tax_id: str | None
    object_url: str
    surface_title: str
    goal_title: str
    budget_text: str
    term_text: str
    ad_account_id: int
    ad_account_title: str
    ad_account_external_id: str
    ad_account_client_id: int | None
    ad_account_client_name: str | None
    ad_account_balance_rub: str | None
    daily_budget_rub: float | None
    balance_below_daily_budget: bool
    client_mismatch: bool


async def launch_preview(
    session: AsyncSession,
    account_id: int,
    brief_id: int,
    *,
    ad_account_id: int | None = None,
    goal: str | None = None,
    settings: Settings | None = None,
) -> LaunchPreview:
    """Сводка перед запуском без креатива — только чтение, кампанию не создаёт и
    не запускает; сам запуск остаётся отдельным явным вызовом
    (`launch_without_creative`/`launch_from_creative`).

    Кабинет выбирается тем же правилом, что и реальный запуск
    (`_resolve_ad_account` выше): явно оператором либо единственный активный
    (`resolve_default_account`) — те же ошибки выбора кабинета
    (`AccountNotFoundError`, `NoAdAccountError`, `AmbiguousAdAccountError`,
    `TokenUnavailableError`, `NotConfiguredError`) значат то же самое, что и при
    запуске: маппинг в HTTP — дело роутера.

    Данные клиента/объекта/площадки и сырой текст бюджета/срока берутся из
    `services.brief_view.get_brief_card` — той же карточки, что показывает бот
    (`services.brief_view.field_value`/`tax_id` — публичные версии приватных
    `_field_value`/`_tax_id` бота, см. их докстринги). Предупреждение о балансе —
    той же формулой, что и реальный запуск (`services.launch.
    balance_below_daily_budget`/`daily_budget_rub_from_amount`, перенесены туда
    именно затем, чтобы бот и веб считали одинаково); признак чужого кабинета —
    `ad_account_client_mismatch` выше, читающая версия того же правила, что
    жёстко проверяет `_check_ad_account_matches_brief` при реальном запуске.

    `goal` — цель, которую оператор явно выбрал на отдельном шаге мастера запуска
    (сценарий с креативом: «Подписчики»/«Сообщения»/«Заявки — лид-форма»/«Заявка
    через Senler», те же коды, что отдаёт `services.goals.launch_goals()`).
    Не передан — сводка показывает цель запуска без креатива
    (`card.launch_goal_title`, правило `services.goals.NO_CREATIVE_GOAL`), как и
    раньше. Передан — сводка показывает именно её (`services.goals.
    launch_goal_title`), а не всегда «Подписчики». Проверяется тем же
    `_validate_goal`, что и реальный запуск — неизвестный или ещё не
    реализованный код бросает `UnsupportedGoalError` вместо молчаливой подмены.

    Бросает `BriefNotFoundError`, если брифа нет у тенанта, `UnsupportedGoalError`,
    если передан нереализованный или неизвестный `goal`.
    """
    _validate_goal(goal)
    cfg = settings or get_settings()
    card = await get_brief_card(session, account_id, brief_id)
    if card is None:
        raise BriefNotFoundError(str(brief_id))

    ad_account, _token = await _resolve_ad_account(session, account_id, ad_account_id, cfg)

    budget_text = field_value(card, "Бюджет")
    amount, needs_discussion = parse_budget(budget_text)
    daily_budget = daily_budget_rub_from_amount(amount, needs_discussion)

    balance_warning = False
    if ad_account.balance_rub:
        try:
            balance = float(ad_account.balance_rub)
        except ValueError:
            balance = None
        if balance is not None:
            balance_warning = balance_below_daily_budget(balance, amount, needs_discussion)

    return LaunchPreview(
        client_name=card.client_name,
        client_tax_id=tax_id(card) or None,
        object_url=field_value(card, "Ссылка на страницу VK", "Ссылка на объект продвижения"),
        surface_title=card.surface_title,
        goal_title=launch_goal_title(goal) if goal is not None else card.launch_goal_title,
        budget_text=budget_text,
        term_text=field_value(card, "Срок / период"),
        ad_account_id=ad_account.id,
        ad_account_title=ad_account.title,
        ad_account_external_id=ad_account.external_id,
        ad_account_client_id=ad_account.client_id,
        ad_account_client_name=ad_account.client_name,
        ad_account_balance_rub=ad_account.balance_rub,
        daily_budget_rub=daily_budget,
        balance_below_daily_budget=balance_warning,
        client_mismatch=ad_account_client_mismatch(ad_account, card.client_id),
    )


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


def _community_reference(object_url: str) -> tuple[str | None, str]:
    """Разобрать ссылку на сообщество из брифа: (числовой id или `None`, короткий адрес).

    Последний сегмент пути, без учёта регистра. Клиенты почти всегда присылают
    короткий адрес (`https://vk.ru/djbeauty` -> `djbeauty`) — числового id там
    просто нет, и раньше проверка на этом молча сдавалась. Префиксы `club`/
    `public`/`event`/`id` перед числом отбрасываются, остаётся сам номер;
    остальное — короткий адрес как есть. Оба признака идут в
    `db.community_tokens.find_decrypted_token`: она сама решает, по какому
    совпало (домены `vk.com` и `vk.ru` — оба обычные http(s)-ссылки, второй
    парсинг для них не нужен).
    """
    raw = object_url.strip()
    if "//" not in raw:
        raw = f"https://{raw}"
    segments = [segment for segment in urlsplit(raw).path.split("/") if segment]
    if not segments:
        return None, ""
    slug = segments[-1].lower()
    match = _COMMUNITY_SLUG_RE.match(slug)
    numeric_id = match.group(1) if match else None
    return numeric_id, slug


async def _verify_senler(
    session: AsyncSession, account_id: int, spec: CampaignSpec, settings: Settings
) -> str | None:
    """Проверить подключение Senler ПЕРЕД запуском цели «Заявка через Senler».

    Три исхода:
    - Senler явно НЕ подключён (токен есть, VK это подтвердил) —
      `SenlerNotConnectedError`, кампания не создаётся;
    - подключён — `None`, запуск продолжается молча;
    - проверить нечем (сообщество из ссылки не сопоставилось ни с одним
      привязанным токеном — ни по числовому id, ни по короткому адресу, —
      либо сходить в VK не вышло, либо сам поиск токена споткнулся об аномалию
      БД) — возвращаем предупреждение, запуск всё равно продолжается: требовать
      токен с каждого клиента мы не будем, но и выдавать непроверенное за
      проверенное нельзя (CLAUDE.md §7). Проверка Senler — страховка, а не
      критический путь: её внутренний отказ не должен ронять запуск кампании
      (ревью 2026-08-24, дефект 1, пункт 3), поэтому ошибку поиска (в т.ч.
      любую неучтённую аномалию данных) ловим и деградируем так же честно, как
      уже обрабатывается недоступность VK ниже — но не молча: логируем, чтобы
      причина не потерялась.
    """
    numeric_id, slug = _community_reference(spec.object_url)
    try:
        match = await find_decrypted_token(
            session, account_id, community_id=numeric_id, screen_name=slug, settings=settings
        )
    except Exception:  # noqa: BLE001 — поиск токена не должен ронять запуск, это подстраховка
        logger.exception(
            "senler token lookup failed for community reference (%s, %s)", numeric_id, slug
        )
        return _SENLER_UNVERIFIED_NOTE
    if match is None:
        return _SENLER_UNVERIFIED_NOTE
    try:
        servers = await fetch_callback_servers(match.token, match.community_id)
    except VkCommunityUnreachable:
        logger.warning("senler check unreachable for community %s", match.community_id)
        return _SENLER_UNVERIFIED_NOTE
    if not detect_senler(servers).connected:
        raise SenlerNotConnectedError(match.community_id)
    return None


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
    allow_relaunch: bool = False,
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

    `allow_relaunch` — оператор явно подтвердил повторный запуск по брифу, у
    которого уже есть незавершённая кампания на боевом канале (spec §F). Без
    него второй такой запуск отклоняется `CampaignAlreadyExistsError` —
    см. `_check_no_active_campaign`.

    Бросает `BriefNotFoundError`, если брифа нет, `BriefValidationError`
    (из `parse_brief`), `UnsupportedGoalError` (неподдержанный параметр `goal`
    ИЛИ неподдержанная цель самого брифа), ошибки выбора кабинета
    (`AccountNotFoundError`, `TokenUnavailableError`, `NoAdAccountError`,
    `AmbiguousAdAccountError`), сверки кабинета с брифом (`AdAccountClientMismatchError`,
    `AdvertiserMismatchError`, spec 2026-08-25-cabinet-client-binding-design §1.2-1.3) и
    `CampaignAlreadyExistsError` (повтор без `allow_relaunch`, spec §F) — все они
    срабатывают ДО записи `Creative`/`Cabinet`/`Campaign` и до обращения к площадке
    (см. `_check_ad_account_matches_brief`, `_check_no_active_campaign`).
    """
    cfg = settings or get_settings()
    _validate_goal(goal)
    brief = await get_brief(session, account_id, brief_id)
    if brief is None:
        raise BriefNotFoundError(str(brief_id))

    # Кабинет разбираем до брифа: его пригодность от брифа не зависит, а оператор
    # должен узнать про мёртвый доступ сразу, а не после разбора полей.
    ad_account, vk_token = await _resolve_ad_account(session, account_id, ad_account_id, cfg)

    # Разбор УЖЕ СОХРАНЁННОГО брифа, не приём нового — `require_tax_id=False`
    # явно: среди старых брифов физлиц есть такие, где ИНН не спрашивали
    # вовсе (обязательность введена позже, решение 2026-08-25), и требовать
    # его задним числом при запуске кампании нельзя. `parse_brief` по
    # умолчанию строгий (`require_tax_id=True`) — это единственное, видимое
    # глазами послабление для разбора исторических данных.
    parsed = parse_brief(brief.payload, BriefVariant(brief.variant), require_tax_id=False)

    # Сверка ДО любых побочных эффектов (Creative/Cabinet/кампания на площадке):
    # чужой кабинет или несовпавший ИНН обязаны прервать запуск начисто (spec §1.2-1.3).
    _check_ad_account_matches_brief(ad_account, brief.client_id, parsed.tax_id)

    # Блокировка строки брифа (no-op на SQLite) держит проверку и создание кампании
    # в одной транзакции: два одновременных запуска по одному брифу не должны оба
    # проскочить проверку параллельно (spec §F, гонка двух запросов). Возвращаемая
    # строка намеренно не используется — сам факт блокировки и есть эффект, `brief`
    # выше уже разобран для запуска.
    await lock_brief_for_launch(session, account_id, brief_id)
    await _check_no_active_campaign(session, account_id, brief_id, allow_relaunch=allow_relaunch)

    try:
        spec = build_campaign_spec(parsed)
    except UnsupportedBriefGoalError as exc:
        # Транслируем в тот же тип, что и неподдержанный параметр `goal` (`_validate_goal`
        # выше): роутерам и боту достаточно ловить один `UnsupportedGoalError`, чтобы
        # честно ответить 422 вместо утечки 500 в VK.
        raise UnsupportedGoalError(exc.goal.value) from exc

    senler_note: str | None = None
    if parsed.goal is Goal.SENLER:
        # Проверяем ДО любых побочных эффектов (Creative/Cabinet/кампания): явный
        # отказ (`SenlerNotConnectedError`) обязан прервать запуск начисто.
        senler_note = await _verify_senler(session, account_id, spec, cfg)

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
    if senler_note:
        message = f"{message}\n{senler_note}"
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
