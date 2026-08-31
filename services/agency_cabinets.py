"""Заведение клиенту рекламного кабинета VK через агентский доступ (B2/B3).

План: docs/superpowers/plans/2026-08-25-agency-cabinets.md, волна B. Единая
операция `create_client_cabinet`: завести клиента у агентства VK
(`AgencyCabinetAdapter.create_agency_client`) → выпустить на него токен без
подтверждения клиента (`integrations.vk_oauth.request_agency_client_token`,
`grant_type=agency_client_credentials`) → сохранить как `AdAccount`,
закреплённый за клиентом (`services.ad_accounts.add_account`).

Модуль не знает про бриф/парсер брифа (`services.brief_parser`,
`services.briefs`) — по ним параллельно идёт другая задача (обязательность
ИНН). Имя рекламодателя (`full_name`) и его ИНН (`tax_id`) приходят уже
готовыми параметрами: их извлечение из брифа и подтверждение оператором —
дело вызывающего кода (карточка подтверждения, волна C плана).

Кто вызывает `/agency/clients.json` от имени агентства: тот же токен, что
бутстрапит единственный на сегодня «общий» кабинет оператора
(`Settings.vk_ads_access_token`, см. `services.ad_accounts.seed_from_env`) —
это и есть собственный VK Ads аккаунт агентства (план 2026-08-25, раздел
«Контекст»: агентский статус получила Анастасия на СВОЙ кабинет). Отдельного
реестра «какой AdAccount — мастер-агентский» в системе нет и добавлять его
здесь не стали (см. отчёт задачи): `resolve_default_account` для этой роли не
подходит — он требует РОВНО ОДИН активный кабинет тенанта и начинает падать
`AmbiguousAdAccountError`, как только появляется первый клиентский кабинет,
а появляться они будут постоянно, в этом весь смысл фичи.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from config.settings import Settings, get_settings
from db.repositories import get_client
from integrations.adapter import AgencyCabinetAdapter
from integrations.vk_api import VkAgencyClientUnavailable, VkApiAdapter
from integrations.vk_oauth import (
    VkOAuthError,
    VkOAuthNotConfigured,
    VkOAuthUnavailable,
    request_agency_client_token,
)
from sqlalchemy.ext.asyncio import AsyncSession

from services.ad_accounts import (
    ADVERTISER_THIRD_PARTY,
    AdAccountView,
    ClientNotFoundError,
    add_account,
)
from services.secret_box import NotConfiguredError, SecretBox
from services.vk_identity import InvalidTokenError, VkUnreachableError

logger = logging.getLogger(__name__)

T = TypeVar("T")


class AgencyCabinetError(Exception):
    """Базовая ошибка заведения клиенту рекламного кабинета VK (B2/B3)."""


class AgencyDisabledError(AgencyCabinetError):
    """Предохранитель `vk_agency_confirmed` выключен (CLAUDE.md §1.4): боевое
    создание кабинетов VK не выполняется, пока агентский статус не подтверждён.
    """


class AgencyMissingTaxIdError(AgencyCabinetError):
    """ИНН не указан — без него кабинет не заводим: закон о рекламе требует
    указывать конечного рекламодателя (план 2026-08-25, задача A2)."""


class AgencyTokenIssuanceFailedError(AgencyCabinetError):
    """Клиент уже заведён в VK (`vk_client_id`/`vk_username` существуют), но
    выпустить на него токен не удалось — половинчатое состояние. Причина в
    `__cause__`; `vk_client_id`/`vk_username` даны, чтобы повторная попытка
    выпуска токена могла сослаться на уже созданного клиента, не заводя в VK
    дубль.
    """

    def __init__(self, vk_client_id: str, vk_username: str | None) -> None:
        self.vk_client_id = vk_client_id
        self.vk_username = vk_username
        super().__init__(f"VK client {vk_client_id} was created but token issuance failed")


class AgencyCabinetPersistError(AgencyCabinetError):
    """Клиент заведён и токен выпущен, но сохранить кабинет как `AdAccount` не
    удалось (VK не подтвердил свежевыпущенный токен живым запросом, либо
    пропал ключ шифрования на середине операции) — тоже половинчатое
    состояние. Причина в `__cause__`; `vk_client_id`/`vk_username` — тот же
    смысл, что у `AgencyTokenIssuanceFailedError`. Токен, который не удалось
    сохранить, нигде не оседает — повторный запуск операции выпустит новый
    (в пределах лимита VK на пять живых токенов клиента).
    """

    def __init__(self, vk_client_id: str, vk_username: str | None) -> None:
        self.vk_client_id = vk_client_id
        self.vk_username = vk_username
        super().__init__(f"VK client {vk_client_id} cabinet could not be persisted")


async def _retry_once(operation: Callable[[], Awaitable[T]], *, retry_on: type[Exception]) -> T:
    """Ровно одна повторная попытка при моргнувшей сети (план §3 «Две попытки»).

    Отказ по существу (нет прав, ошибка валидации — любой другой тип
    исключения) здесь не перехватывается и улетает с первой попытки: повторять
    его бессмысленно.
    """
    try:
        return await operation()
    except retry_on:
        logger.warning("VK request failed, retrying once: %s", retry_on.__name__)
        return await operation()


def _cabinet_name(full_name: str, niche: str | None) -> str:
    """Имя кабинета для VK: ФИО (или название компании) — основа; ниша
    дописывается, только если она известна (решение заказчика, план §4).

    Автоматическое определение ниши по категории сообщества VK сюда сознательно
    НЕ добавлено (см. отчёт задачи B2/B3): для этого понадобился бы отдельный
    токен другой, не рекламной, VK API (Groups API, другой хост и другое
    приложение) — новая зависимость и новый секрет, которых план явно просит не
    заводить, если чисто не получается. Без ниши имя остаётся из одного ФИО —
    решение допускает это как нормальный исход, схема не ломается.
    """
    name = full_name.strip()
    trimmed_niche = niche.strip() if niche else ""
    return f"{name} — {trimmed_niche}" if trimmed_niche else name


async def create_client_cabinet(
    session: AsyncSession,
    account_id: int,
    client_id: int,
    *,
    full_name: str,
    tax_id: str | None,
    niche: str | None = None,
    settings: Settings | None = None,
    agency_adapter: AgencyCabinetAdapter | None = None,
) -> AdAccountView:
    """Завести клиенту рекламный кабинет VK: клиент агентства → токен → `AdAccount`.

    Единая операция (B2): `agency_adapter.create_agency_client` заводит клиента
    у агентства → `request_agency_client_token` выпускает на него токен без
    подтверждения клиента (`grant_type=agency_client_credentials`) →
    `services.ad_accounts.add_account` проверяет токен живым запросом,
    шифрует и сохраняет `AdAccount` с проставленным `client_id` и
    `advertiser_kind=third_party` (конечный рекламодатель — сам клиент, не
    оператор: `advertiser_name`/`advertiser_inn` заполняются из `full_name`/
    `tax_id`).

    Предохранители, оба обязательны: `settings.vk_agency_confirmed` (иначе
    `AgencyDisabledError`) и непустой `tax_id` (иначе `AgencyMissingTaxIdError`)
    — обе проверки идут ДО первого сетевого вызова, вместе с проверкой, что
    `client_id` существует у тенанта (`ClientNotFoundError`), что настроен
    ключ шифрования (`NotConfiguredError`) и OAuth-приложение агентства
    (`VkOAuthNotConfigured`) — чтобы не тратить операцию VK впустую там, где
    заведомо нечем будет сохранить результат.

    Сеть: у создания клиента и у выпуска токена — по одной повторной попытке
    при `VkAgencyClientUnavailable`/`VkOAuthUnavailable` (план §3, «ровно две
    попытки, не больше»); отказ по существу (`VkAgencyClientForbidden`,
    `VkAgencyClientValidationError`, `VkOAuthInvalidCredentials`,
    `VkOAuthRejected`, …) не повторяется и улетает наверх как есть — это
    типизированные исключения `integrations.vk_api`/`integrations.vk_oauth`,
    ловите их отдельно там, где нужен свой текст для оператора.

    Половинчатые состояния — «клиент в VK уже есть, а кабинета у нас нет» —
    не имитируются успехом: `AgencyTokenIssuanceFailedError` (клиент заведён,
    токен не выпущен) и `AgencyCabinetPersistError` (токен выпущен, но
    сохранить не удалось) несут `vk_client_id`/`vk_username`, но никогда сам
    токен — он никуда, кроме зашифрованной колонки, не попадает.

    `agency_adapter` — для тестов и для явного выбора площадки; по умолчанию
    строится `VkApiAdapter` на `settings.vk_ads_access_token` — собственном
    токене агентства (см. модульную документацию, почему именно он).
    """
    cfg = settings or get_settings()

    if not cfg.vk_agency_confirmed:
        raise AgencyDisabledError(
            "vk_agency_confirmed is off: live VK agency cabinet creation is disabled"
        )
    cleaned_tax_id = (tax_id or "").strip()
    if not cleaned_tax_id:
        raise AgencyMissingTaxIdError("tax_id (ИНН) is required to create a client cabinet")

    if await get_client(session, account_id, client_id) is None:
        raise ClientNotFoundError(str(client_id))

    box = SecretBox(cfg.vk_ads_secret_key.get_secret_value())
    if not box.configured:
        raise NotConfiguredError("VK_ADS_SECRET_KEY is empty")

    oauth_client_id = cfg.vk_ads_client_id.get_secret_value()
    oauth_client_secret = cfg.vk_ads_client_secret.get_secret_value()
    if not oauth_client_id.strip() or not oauth_client_secret.strip():
        raise VkOAuthNotConfigured("vk_ads_client_id/vk_ads_client_secret are not configured")

    adapter = agency_adapter or VkApiAdapter(cfg.vk_ads_access_token)
    name = _cabinet_name(full_name, niche)

    vk_client = await _retry_once(
        lambda: adapter.create_agency_client(client_name=name, client_info=f"ИНН {cleaned_tax_id}"),
        retry_on=VkAgencyClientUnavailable,
    )
    logger.info(
        "VK agency client created: vk_client_id=%s username=%s",
        vk_client.client_id,
        vk_client.username,
    )

    try:
        token = await _retry_once(
            lambda: request_agency_client_token(
                oauth_client_id,
                oauth_client_secret,
                agency_client_name=vk_client.username or None,
                agency_client_id=None if vk_client.username else vk_client.client_id,
            ),
            retry_on=VkOAuthUnavailable,
        )
    except VkOAuthError as exc:
        raise AgencyTokenIssuanceFailedError(vk_client.client_id, vk_client.username) from exc

    try:
        view = await add_account(
            session,
            account_id,
            token.access_token.get_secret_value(),
            title=name,
            refresh_token=token.refresh_token.get_secret_value(),
            advertiser_kind=ADVERTISER_THIRD_PARTY,
            advertiser_name=full_name.strip(),
            advertiser_inn=cleaned_tax_id,
            client_id=client_id,
            settings=cfg,
        )
    except (InvalidTokenError, VkUnreachableError, NotConfiguredError) as exc:
        raise AgencyCabinetPersistError(vk_client.client_id, vk_client.username) from exc

    logger.info(
        "client cabinet created: ad_account_id=%s client_id=%s vk_client_id=%s",
        view.id,
        client_id,
        vk_client.client_id,
    )
    return view


__all__ = [
    "AgencyCabinetError",
    "AgencyCabinetPersistError",
    "AgencyDisabledError",
    "AgencyMissingTaxIdError",
    "AgencyTokenIssuanceFailedError",
    "create_client_cabinet",
]
