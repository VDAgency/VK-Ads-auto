"""Рекламные кабинеты оператора: добавление, health-check, выбор, удаление.

Спека: docs/superpowers/specs/2026-07-27-multi-cabinet-design.md §8.

Единственное место в системе, где токен кабинета расшифровывается. Наружу
(в API, бота, веб и логи) уходит только `AdAccountView` — там вместо токена
хвост из четырёх символов.

Проверено живыми запросами 2026-07-27: параметр `sudo` работает только в
веб-интерфейсе VK, через API игнорируется. Поэтому кабинетов много и у каждого
свой токен, а не один агентский на всех.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TypeVar

from config.settings import Settings, get_settings
from db.models import AdAccount
from db.repositories import (
    archive_ad_account,
    count_active_ad_accounts,
    create_ad_account,
    find_active_ad_account_by_external_id,
    get_ad_account,
    get_client,
    list_ad_accounts,
    list_ad_accounts_for_client,
    set_ad_account_client,
    set_ad_account_health,
    set_ad_account_tokens,
)
from integrations.vk_oauth import VkOAuthError, VkOAuthUnavailable, refresh_agency_token
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from services.secret_box import NotConfiguredError, SecretBox, token_tail
from services.vk_identity import (
    InvalidTokenError,
    VkUnreachableError,
    fetch_balance,
    fetch_identity,
)

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Чью рекламу размещаем в кабинете (spec §5). Признак информационный:
# маркировку (erid/ЕРИР) присваивает сама площадка, через API её не задать.
ADVERTISER_OWNER = "owner"
ADVERTISER_THIRD_PARTY = "third_party"
ADVERTISER_KINDS = (ADVERTISER_OWNER, ADVERTISER_THIRD_PARTY)

# Состояния health-check. `unauthorized` и `error` разведены намеренно: первое
# означает «токен точно не годится», второе — «мы не дозвонились до VK».
HEALTH_UNKNOWN = "unknown"
HEALTH_HEALTHY = "healthy"
HEALTH_UNAUTHORIZED = "unauthorized"
HEALTH_ERROR = "error"


class AdAccountError(Exception):
    """Базовая ошибка работы с рекламными кабинетами."""


class DuplicateAccountError(AdAccountError):
    """Кабинет с таким VK-id уже добавлен и активен."""

    def __init__(self, external_id: str) -> None:
        super().__init__(external_id)
        self.external_id = external_id


class AccountNotFoundError(AdAccountError):
    """Кабинета нет у этого тенанта."""


class TokenUnavailableError(AdAccountError):
    """У кабинета нет пригодного токена (архивный или ключ шифрования сменился)."""


class TokenRefreshUnavailableError(AdAccountError):
    """У кабинета нет ключа обновления — обновить токен нечем (B3).

    Отличать от `TokenRefreshFailedError`: здесь `refresh_encrypted` попросту
    пуст (старый кабинет, добавленный без него, либо архивный), запрос к VK
    даже не уходит.
    """


class TokenRefreshFailedError(AdAccountError):
    """VK отказал в обновлении токена по ключу обновления (B3).

    Причина — в `__cause__` (`VkOAuthInvalidCredentials`/`VkOAuthRejected`/
    `VkOAuthUnavailable` после повтора, см. `integrations.vk_oauth`).
    """


class NoAdAccountError(AdAccountError):
    """Нет ни одного активного рекламного кабинета."""


class AmbiguousAdAccountError(AdAccountError):
    """Кабинетов несколько, а оператор не выбрал ни одного."""


class ClientNotFoundError(AdAccountError):
    """Клиент, которому хотят привязать кабинет, не найден у этого тенанта."""

    def __init__(self, client_id: str) -> None:
        super().__init__(client_id)
        self.client_id = client_id


@dataclass(frozen=True, slots=True)
class AdAccountView:
    """Кабинет, каким его можно показывать наружу. Токена здесь нет и быть не может."""

    id: int
    title: str
    external_id: str
    username: str | None
    token_tail: str
    advertiser_kind: str
    advertiser_name: str | None
    advertiser_inn: str | None
    client_id: int | None
    client_name: str | None
    status: str
    health: str
    health_checked_at: datetime | None
    health_error: str | None
    balance_rub: str | None

    @property
    def is_usable(self) -> bool:
        """Можно ли запускать в этот кабинет. `error` не запрещаем: VK мог просто моргнуть."""
        return self.status == "active" and self.health != HEALTH_UNAUTHORIZED


def _view(row: AdAccount, *, client_name: str | None = None) -> AdAccountView:
    return AdAccountView(
        id=row.id,
        title=row.title,
        external_id=row.external_id,
        username=row.username,
        token_tail=row.token_tail,
        advertiser_kind=row.advertiser_kind,
        advertiser_name=row.advertiser_name,
        advertiser_inn=row.advertiser_inn,
        client_id=row.client_id,
        client_name=client_name,
        status=row.status,
        health=row.health,
        health_checked_at=row.health_checked_at,
        health_error=row.health_error,
        balance_rub=row.balance_rub,
    )


async def _view_with_client(
    session: AsyncSession, account_id: int, row: AdAccount
) -> AdAccountView:
    """Представление кабинета с именем привязанного клиента (`Client.full_name`).

    Кабинет без привязки — `client_name=None`, и это не заглушка, а корректное
    значение «общий кабинет» (решение проекта — не выдумывать «Без имени» на
    уровне сервиса, это дело интерфейса).
    """
    name: str | None = None
    if row.client_id is not None:
        client = await get_client(session, account_id, row.client_id)
        name = client.full_name if client is not None else None
    return _view(row, client_name=name)


async def _resolve_client_name(
    session: AsyncSession, account_id: int, client_id: int | None
) -> str | None:
    """Проверить, что клиент существует у тенанта, и вернуть его имя.

    `client_id=None` — кабинет остаётся общим, проверять нечего. Иначе клиент
    обязан существовать: привязка к чужому/несуществующему id — опечатка
    оператора, а не новый клиент.
    """
    if client_id is None:
        return None
    client = await get_client(session, account_id, client_id)
    if client is None:
        raise ClientNotFoundError(str(client_id))
    return client.full_name


def _box(settings: Settings) -> SecretBox:
    return SecretBox(settings.vk_ads_secret_key.get_secret_value())


def _normalize_kind(kind: str | None) -> str:
    """Неизвестное значение трактуем как «реклама владельца» — безопасный дефолт."""
    return kind if kind in ADVERTISER_KINDS else ADVERTISER_OWNER


async def add_account(
    session: AsyncSession,
    account_id: int,
    token: str,
    *,
    title: str | None = None,
    refresh_token: str | None = None,
    advertiser_kind: str = ADVERTISER_OWNER,
    advertiser_name: str | None = None,
    advertiser_inn: str | None = None,
    client_id: int | None = None,
    settings: Settings | None = None,
) -> AdAccountView:
    """Добавить кабинет по токену.

    Токен проверяется живым запросом ДО записи в БД: иначе оператор увидел бы
    «кабинет добавлен» и узнал правду только при запуске кампании. Название, id
    и логин берутся из ответа VK — вводить их руками не нужно.

    `client_id` — необязательная привязка к клиенту (spec 2026-08-25 §1.1):
    пусто заводит общий кабинет (текущее поведение), заполнено — закрепляет
    кабинет за клиентом, и он перестаёт быть виден чужим брифам.

    Бросает `InvalidTokenError`, `VkUnreachableError`, `DuplicateAccountError`,
    `ClientNotFoundError` (указанного клиента нет у тенанта) и
    `NotConfiguredError` (не задан ключ шифрования). Коммит — на вызывающем.
    """
    cfg = settings or get_settings()
    box = _box(cfg)
    if not box.configured:
        raise NotConfiguredError("VK_ADS_SECRET_KEY is empty")
    client_name = await _resolve_client_name(session, account_id, client_id)

    token = token.strip()
    identity = await fetch_identity(token)

    existing = await find_active_ad_account_by_external_id(
        session, account_id, identity.external_id
    )
    if existing is not None:
        raise DuplicateAccountError(identity.external_id)

    kind = _normalize_kind(advertiser_kind)
    row = await create_ad_account(
        session,
        account_id,
        title=(title or identity.title).strip() or identity.title,
        external_id=identity.external_id,
        username=identity.username,
        token_encrypted=box.encrypt(token),
        refresh_encrypted=box.encrypt(refresh_token.strip()) if refresh_token else None,
        token_tail=token_tail(token),
        advertiser_kind=kind,
        advertiser_name=advertiser_name if kind == ADVERTISER_THIRD_PARTY else None,
        advertiser_inn=advertiser_inn if kind == ADVERTISER_THIRD_PARTY else None,
        client_id=client_id,
        health=HEALTH_HEALTHY,
        balance_rub=await fetch_balance(token),
    )
    logger.info(
        "ad account added: id=%s external_id=%s kind=%s client_id=%s",
        row.id,
        row.external_id,
        row.advertiser_kind,
        row.client_id,
    )
    return _view(row, client_name=client_name)


def _is_stale(row: AdAccount, ttl_minutes: int) -> bool:
    """Пора ли перепроверять кабинет (кеш вместо фонового опроса — VK держит 3 rps)."""
    if row.health == HEALTH_UNKNOWN or row.health_checked_at is None:
        return True
    checked = row.health_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    return datetime.now(UTC) - checked > timedelta(minutes=ttl_minutes)


async def _build_views(
    session: AsyncSession,
    account_id: int,
    rows: Sequence[AdAccount],
    *,
    refresh_stale: bool,
    settings: Settings,
) -> list[AdAccountView]:
    """Представления по строкам: общая часть `list_accounts`/`list_accounts_for_client`."""
    if not refresh_stale:
        return [await _view_with_client(session, account_id, row) for row in rows]

    views: list[AdAccountView] = []
    for row in rows:
        if _is_stale(row, settings.ad_account_health_ttl_minutes):
            views.append(await check_health(session, account_id, row.id, settings=settings))
        else:
            views.append(await _view_with_client(session, account_id, row))
    return views


async def list_accounts(
    session: AsyncSession,
    account_id: int,
    *,
    refresh_stale: bool = True,
    settings: Settings | None = None,
) -> list[AdAccountView]:
    """Кабинеты тенанта без токенов; устаревшие health-check обновляются по пути."""
    cfg = settings or get_settings()
    rows = await list_ad_accounts(session, account_id)
    return await _build_views(session, account_id, rows, refresh_stale=refresh_stale, settings=cfg)


async def list_accounts_for_client(
    session: AsyncSession,
    account_id: int,
    client_id: int,
    *,
    refresh_stale: bool = True,
    settings: Settings | None = None,
) -> list[AdAccountView]:
    """Кабинеты, пригодные клиенту (spec 2026-08-25 §1.1): общие плюс закреплённые за ним.

    Фундамент для сверки при запуске (Т2) и для выбора кабинета в карточке
    подтверждения (Т3): список сужен до того, чем клиенту действительно можно
    запускать рекламу — общий кабинет без привязки виден всем, закреплённый
    доступен только своему.
    """
    cfg = settings or get_settings()
    rows = await list_ad_accounts_for_client(session, account_id, client_id)
    return await _build_views(session, account_id, rows, refresh_stale=refresh_stale, settings=cfg)


async def get_account(
    session: AsyncSession, account_id: int, ad_account_id: int
) -> AdAccountView | None:
    """Один кабинет тенанта (без токена). `None` — нет такого."""
    row = await get_ad_account(session, account_id, ad_account_id)
    return None if row is None else await _view_with_client(session, account_id, row)


async def set_account_client(
    session: AsyncSession,
    account_id: int,
    ad_account_id: int,
    client_id: int | None,
    *,
    settings: Settings | None = None,
) -> AdAccountView:
    """Изменить привязку существующего кабинета к клиенту.

    `client_id=None` снова делает кабинет общим. Бросает `ClientNotFoundError`,
    если указанный клиент не найден у тенанта, и `AccountNotFoundError`, если
    нет такого кабинета.
    """
    await _resolve_client_name(session, account_id, client_id)
    row = await set_ad_account_client(session, account_id, ad_account_id, client_id)
    if row is None:
        raise AccountNotFoundError(str(ad_account_id))
    logger.info("ad account client binding changed: id=%s client_id=%s", ad_account_id, client_id)
    return await _view_with_client(session, account_id, row)


async def _retry_once(operation: Callable[[], Awaitable[T]], *, retry_on: type[Exception]) -> T:
    """Ровно одна повторная попытка при моргнувшей сети (план B2/B3 §3): «две
    попытки, не больше». Отказ по существу (любой другой тип исключения) здесь
    не перехватывается вовсе и улетает с первой попытки — повторять его
    бессмысленно.
    """
    try:
        return await operation()
    except retry_on:
        logger.warning("VK request failed, retrying once: %s", retry_on.__name__)
        return await operation()


async def _probe(token: str) -> tuple[str, str | None, str | None]:
    """Живой опрос VK по токену → (health, error, balance). Общая часть
    `check_health` — вынесена, чтобы после обновления токена (B3) перепроверить
    новым токеном, не дублируя логику разбора ответа."""
    try:
        identity = await fetch_identity(token)
        balance = await fetch_balance(token)
        if identity.status not in ("active", "unknown"):
            return HEALTH_ERROR, f"VK account status: {identity.status}", balance
        return HEALTH_HEALTHY, None, balance
    except InvalidTokenError:
        return HEALTH_UNAUTHORIZED, "VK отклонил токен — выпустите новый", None
    except VkUnreachableError as exc:
        return HEALTH_ERROR, str(exc)[:255], None


async def check_health(
    session: AsyncSession,
    account_id: int,
    ad_account_id: int,
    *,
    settings: Settings | None = None,
) -> AdAccountView:
    """Проверить кабинет живым запросом и записать результат.

    Архивный кабинет не проверяем: токена у него уже нет (стёрт при удалении).

    Отказ авторизации (401/403) с сохранённым ключом обновления — не сразу
    приговор (B3, план 2026-08-25-agency-cabinets.md): токен VK живёт сутки,
    так что «протух» — обычное дело. Пробуем обновить его РОВНО ОДИН раз
    (`refresh_account_token`) и перепроверяем новым токеном; если обновление
    не удалось или новый токен тоже отклонён — честно помечаем `unauthorized`,
    без дальнейших попыток (иначе риск зациклиться на VK).
    """
    cfg = settings or get_settings()
    row = await get_ad_account(session, account_id, ad_account_id)
    if row is None:
        raise AccountNotFoundError(str(ad_account_id))
    if row.status != "active" or not row.token_encrypted:
        return await _view_with_client(session, account_id, row)

    box = _box(cfg)
    try:
        token = box.decrypt(row.token_encrypted)
    except Exception:  # noqa: BLE001 — сменился ключ: честно помечаем, но не падаем
        logger.exception("cannot decrypt token of ad account %s", ad_account_id)
        updated = await set_ad_account_health(
            session, account_id, ad_account_id, HEALTH_ERROR, error="cannot decrypt stored token"
        )
        return await _view_with_client(session, account_id, updated or row)

    health, error, balance = await _probe(token)
    if health == HEALTH_UNAUTHORIZED and row.refresh_encrypted:
        try:
            await refresh_account_token(session, account_id, ad_account_id, settings=cfg)
        except (
            TokenRefreshUnavailableError,
            TokenRefreshFailedError,
            NotConfiguredError,
            TokenUnavailableError,
        ) as exc:
            logger.warning(
                "token refresh after 401 failed for ad account %s: %s",
                ad_account_id,
                type(exc).__name__,
            )
        else:
            new_token = await resolve_token(session, account_id, ad_account_id, settings=cfg)
            health, error, balance = await _probe(new_token.get_secret_value())

    updated = await set_ad_account_health(
        session, account_id, ad_account_id, health, error=error, balance_rub=balance
    )
    return await _view_with_client(session, account_id, updated or row)


async def refresh_account_token(
    session: AsyncSession,
    account_id: int,
    ad_account_id: int,
    *,
    settings: Settings | None = None,
) -> AdAccountView:
    """Обновить токен кабинета ключом обновления (B3): токен VK живёт сутки,
    `refresh_encrypted` до сих пор заполнялся, но нигде не читался — мёртвое
    поле. Читает его, просит VK новую пару access+refresh
    (`integrations.vk_oauth.refresh_agency_token`, `grant_type=refresh_token`,
    одна повторная попытка при сетевом сбое) и сохраняет обе зашифрованными.
    Кабинет помечается снова `healthy` — как и должно быть после успешного
    обновления живого токена.

    Бросает `AccountNotFoundError`, `TokenRefreshUnavailableError` (нет
    `refresh_encrypted` либо кабинет архивный), `TokenUnavailableError`
    (расшифровать `refresh_encrypted` не удалось — сменился ключ),
    `NotConfiguredError` (не задан `VK_ADS_SECRET_KEY`) и
    `TokenRefreshFailedError` (VK отказал — причина в `__cause__`). Успех и
    отказ различимы явно: полусостояния (обновили, но не сохранили) здесь нет,
    сохранение — последний шаг перед возвратом.
    """
    cfg = settings or get_settings()
    row = await get_ad_account(session, account_id, ad_account_id)
    if row is None:
        raise AccountNotFoundError(str(ad_account_id))
    if row.status != "active" or not row.refresh_encrypted:
        raise TokenRefreshUnavailableError(f"ad account {ad_account_id} has no refresh token")

    box = _box(cfg)
    if not box.configured:
        raise NotConfiguredError("VK_ADS_SECRET_KEY is empty")
    try:
        refresh_token = box.decrypt(row.refresh_encrypted)
    except Exception as exc:  # noqa: BLE001 — сменился ключ шифрования
        raise TokenUnavailableError(
            f"cannot decrypt refresh token of ad account {ad_account_id}"
        ) from exc

    oauth_client_id = cfg.vk_ads_client_id.get_secret_value()
    oauth_client_secret = cfg.vk_ads_client_secret.get_secret_value()
    try:
        new_token = await _retry_once(
            lambda: refresh_agency_token(refresh_token, oauth_client_id, oauth_client_secret),
            retry_on=VkOAuthUnavailable,
        )
    except VkOAuthError as exc:
        raise TokenRefreshFailedError(
            f"VK refused to refresh the token of ad account {ad_account_id}"
        ) from exc

    access_value = new_token.access_token.get_secret_value()
    updated = await set_ad_account_tokens(
        session,
        account_id,
        ad_account_id,
        token_encrypted=box.encrypt(access_value),
        refresh_encrypted=box.encrypt(new_token.refresh_token.get_secret_value()),
        token_tail=token_tail(access_value),
    )
    if updated is None:
        raise AccountNotFoundError(str(ad_account_id))
    healthy = await set_ad_account_health(session, account_id, ad_account_id, HEALTH_HEALTHY)
    logger.info("ad account token refreshed: id=%s", ad_account_id)
    return await _view_with_client(session, account_id, healthy or updated)


async def resolve_token(
    session: AsyncSession,
    account_id: int,
    ad_account_id: int,
    *,
    settings: Settings | None = None,
) -> SecretStr:
    """Токен кабинета для адаптера. Единственная расшифровка в системе.

    Результат сразу заворачивается в `SecretStr`, чтобы он не всплыл в repr,
    логах и трейсбеках.
    """
    cfg = settings or get_settings()
    row = await get_ad_account(session, account_id, ad_account_id)
    if row is None:
        raise AccountNotFoundError(str(ad_account_id))
    if not row.token_encrypted:
        raise TokenUnavailableError(f"ad account {ad_account_id} has no token")
    box = _box(cfg)
    if not box.configured:
        raise NotConfiguredError("VK_ADS_SECRET_KEY is empty")
    try:
        return SecretStr(box.decrypt(row.token_encrypted))
    except NotConfiguredError:
        raise
    except Exception as exc:  # noqa: BLE001 — сменился ключ шифрования
        raise TokenUnavailableError(f"cannot decrypt token of ad account {ad_account_id}") from exc


async def resolve_default_account(
    session: AsyncSession,
    account_id: int,
    *,
    settings: Settings | None = None,
) -> tuple[AdAccountView, SecretStr]:
    """Кабинет по умолчанию и его токен: единственный активный, иначе явная ошибка.

    Health-check намеренно не обновляем (`refresh_stale=False`): запуск кампании не
    должен ждать похода в VK, а протухший статус здесь ничего не решает.
    """
    cfg = settings or get_settings()
    views = await list_accounts(session, account_id, refresh_stale=False, settings=cfg)
    if not views:
        raise NoAdAccountError("no active ad accounts")
    if len(views) > 1:
        raise AmbiguousAdAccountError(f"{len(views)} active ad accounts, none chosen")
    view = views[0]
    return view, await resolve_token(session, account_id, view.id, settings=cfg)


async def mark_unauthorized(
    session: AsyncSession, account_id: int, ad_account_id: int, reason: str
) -> None:
    """Пометить кабинет мёртвым по 401 из боевого вызова.

    Самый честный сигнал: он приходит из реальной работы, а не из опроса.
    """
    await set_ad_account_health(
        session, account_id, ad_account_id, HEALTH_UNAUTHORIZED, error=reason[:255]
    )


async def delete_account(session: AsyncSession, account_id: int, ad_account_id: int) -> None:
    """Удалить кабинет: убрать из выбора и стереть секреты (решение — мягкое удаление)."""
    row = await archive_ad_account(session, account_id, ad_account_id)
    if row is None:
        raise AccountNotFoundError(str(ad_account_id))
    logger.info("ad account archived: id=%s", ad_account_id)


async def seed_from_env(
    session: AsyncSession,
    account_id: int,
    *,
    settings: Settings | None = None,
) -> AdAccountView | None:
    """Одноразовый посев кабинета из `VK_ADS_ACCESS_TOKEN` (spec §8.4).

    Смысл — переезд без простоя: пока таблица пуста, запуск идёт по токену из
    `.env`, а при первом старте с ключом шифрования кабинет заводится сам.
    Любая ошибка — не падаем: вернём `None` и попробуем на следующем старте.
    """
    cfg = settings or get_settings()
    token = cfg.vk_ads_access_token.get_secret_value().strip()
    if not token:
        return None
    if not _box(cfg).configured:
        logger.warning("VK_ADS_SECRET_KEY is not set — ad account seeding skipped")
        return None
    if await count_active_ad_accounts(session, account_id) > 0:
        return None

    refresh = cfg.vk_ads_refresh_token.get_secret_value().strip() or None
    try:
        view = await add_account(session, account_id, token, refresh_token=refresh, settings=cfg)
    except DuplicateAccountError:
        return None
    except (InvalidTokenError, VkUnreachableError) as exc:
        logger.warning("ad account seeding skipped: %s", type(exc).__name__)
        return None
    logger.info("ad account seeded from environment: id=%s", view.id)
    return view


__all__ = [
    "ADVERTISER_KINDS",
    "ADVERTISER_OWNER",
    "ADVERTISER_THIRD_PARTY",
    "HEALTH_ERROR",
    "HEALTH_HEALTHY",
    "HEALTH_UNAUTHORIZED",
    "HEALTH_UNKNOWN",
    "AccountNotFoundError",
    "AdAccountError",
    "AdAccountView",
    "AmbiguousAdAccountError",
    "ClientNotFoundError",
    "DuplicateAccountError",
    "NoAdAccountError",
    "TokenRefreshFailedError",
    "TokenRefreshUnavailableError",
    "TokenUnavailableError",
    "add_account",
    "check_health",
    "delete_account",
    "get_account",
    "list_accounts",
    "list_accounts_for_client",
    "mark_unauthorized",
    "refresh_account_token",
    "resolve_default_account",
    "resolve_token",
    "seed_from_env",
    "set_account_client",
]
