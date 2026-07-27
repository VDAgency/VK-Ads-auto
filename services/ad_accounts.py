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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from config.settings import Settings, get_settings
from db.models import AdAccount
from db.repositories import (
    archive_ad_account,
    count_active_ad_accounts,
    create_ad_account,
    find_active_ad_account_by_external_id,
    get_ad_account,
    list_ad_accounts,
    set_ad_account_health,
)
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
    status: str
    health: str
    health_checked_at: datetime | None
    health_error: str | None
    balance_rub: str | None

    @property
    def is_usable(self) -> bool:
        """Можно ли запускать в этот кабинет. `error` не запрещаем: VK мог просто моргнуть."""
        return self.status == "active" and self.health != HEALTH_UNAUTHORIZED


def _view(row: AdAccount) -> AdAccountView:
    return AdAccountView(
        id=row.id,
        title=row.title,
        external_id=row.external_id,
        username=row.username,
        token_tail=row.token_tail,
        advertiser_kind=row.advertiser_kind,
        advertiser_name=row.advertiser_name,
        advertiser_inn=row.advertiser_inn,
        status=row.status,
        health=row.health,
        health_checked_at=row.health_checked_at,
        health_error=row.health_error,
        balance_rub=row.balance_rub,
    )


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
    settings: Settings | None = None,
) -> AdAccountView:
    """Добавить кабинет по токену.

    Токен проверяется живым запросом ДО записи в БД: иначе оператор увидел бы
    «кабинет добавлен» и узнал правду только при запуске кампании. Название, id
    и логин берутся из ответа VK — вводить их руками не нужно.

    Бросает `InvalidTokenError`, `VkUnreachableError`, `DuplicateAccountError`
    и `NotConfiguredError` (не задан ключ шифрования). Коммит — на вызывающем.
    """
    cfg = settings or get_settings()
    box = _box(cfg)
    if not box.configured:
        raise NotConfiguredError("VK_ADS_SECRET_KEY is empty")

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
        health=HEALTH_HEALTHY,
        balance_rub=await fetch_balance(token),
    )
    logger.info(
        "ad account added: id=%s external_id=%s kind=%s",
        row.id,
        row.external_id,
        row.advertiser_kind,
    )
    return _view(row)


def _is_stale(row: AdAccount, ttl_minutes: int) -> bool:
    """Пора ли перепроверять кабинет (кеш вместо фонового опроса — VK держит 3 rps)."""
    if row.health == HEALTH_UNKNOWN or row.health_checked_at is None:
        return True
    checked = row.health_checked_at
    if checked.tzinfo is None:
        checked = checked.replace(tzinfo=UTC)
    return datetime.now(UTC) - checked > timedelta(minutes=ttl_minutes)


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
    if not refresh_stale:
        return [_view(row) for row in rows]

    views: list[AdAccountView] = []
    for row in rows:
        if _is_stale(row, cfg.ad_account_health_ttl_minutes):
            views.append(await check_health(session, account_id, row.id, settings=cfg))
        else:
            views.append(_view(row))
    return views


async def get_account(
    session: AsyncSession, account_id: int, ad_account_id: int
) -> AdAccountView | None:
    """Один кабинет тенанта (без токена). `None` — нет такого."""
    row = await get_ad_account(session, account_id, ad_account_id)
    return None if row is None else _view(row)


async def check_health(
    session: AsyncSession,
    account_id: int,
    ad_account_id: int,
    *,
    settings: Settings | None = None,
) -> AdAccountView:
    """Проверить кабинет живым запросом и записать результат.

    Архивный кабинет не проверяем: токена у него уже нет (стёрт при удалении).
    """
    cfg = settings or get_settings()
    row = await get_ad_account(session, account_id, ad_account_id)
    if row is None:
        raise AccountNotFoundError(str(ad_account_id))
    if row.status != "active" or not row.token_encrypted:
        return _view(row)

    box = _box(cfg)
    try:
        token = box.decrypt(row.token_encrypted)
    except Exception:  # noqa: BLE001 — сменился ключ: честно помечаем, но не падаем
        logger.exception("cannot decrypt token of ad account %s", ad_account_id)
        updated = await set_ad_account_health(
            session, account_id, ad_account_id, HEALTH_ERROR, error="cannot decrypt stored token"
        )
        return _view(updated or row)

    health, error, balance = HEALTH_HEALTHY, None, None
    try:
        identity = await fetch_identity(token)
        balance = await fetch_balance(token)
        if identity.status not in ("active", "unknown"):
            health, error = HEALTH_ERROR, f"VK account status: {identity.status}"
    except InvalidTokenError:
        health, error = HEALTH_UNAUTHORIZED, "VK отклонил токен — выпустите новый"
    except VkUnreachableError as exc:
        health, error = HEALTH_ERROR, str(exc)[:255]

    updated = await set_ad_account_health(
        session, account_id, ad_account_id, health, error=error, balance_rub=balance
    )
    return _view(updated or row)


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
    "DuplicateAccountError",
    "TokenUnavailableError",
    "add_account",
    "check_health",
    "delete_account",
    "get_account",
    "list_accounts",
    "mark_unauthorized",
    "resolve_token",
    "seed_from_env",
]
