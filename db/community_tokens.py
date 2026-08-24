"""Хранилище токена сообщества VK для проверки подключения Senler (B2).

Функции доступа для `db/models.py::CommunityToken` — по образцу `AdAccount`
(`db/repositories.py` + `services/ad_accounts.py`), но собраны в одном узком
модуле: обработчик бота (`bot/handlers/senler.py`) сюда не ходит напрямую —
только через тонкий эндпоинт ядра (CLAUDE.md §1.3), а сам эндпоинт зовёт эти
функции.

Шифрование — Fernet (`services.secret_box`), тем же ключом (`VK_ADS_SECRET_KEY`),
что и токены рекламных кабинетов. Расшифрованный токен наружу не возвращается
нигде, кроме `get_decrypted_token`/`find_decrypted_token` — ими пользуется
только проверка подключения перед запуском (`services/launch_service.py`);
результат в БД и логи не попадает.

Клиенты в брифе почти всегда присылают короткий адрес сообщества
(`vk.ru/djbeauty`), а не числовой id, поэтому поиск токена под запуск
(`find_decrypted_token`) сопоставляет сообщество по ЛЮБОМУ из двух признаков —
числовому id или короткому адресу (`screen_name`), тем же приёмом, что
`db/repositories.py::find_client_by_contacts`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from config.settings import Settings, get_settings
from services.secret_box import SecretBox
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import CommunityToken

logger = logging.getLogger(__name__)

ACTIVE = "active"
ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class CommunityTokenInfo:
    """Метаданные привязанного токена сообщества — без самого секрета."""

    id: int
    community_id: str
    screen_name: str
    community_name: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class CommunityTokenMatch:
    """Итог поиска токена под запуск: расшифрованный токен + КАНОНИЧЕСКИЙ
    числовой id сообщества (даже если нашли по короткому адресу) — он же нужен
    `groups.getCallbackServers` (`integrations/vk_community.py`)."""

    token: str
    community_id: str


def _box(settings: Settings) -> SecretBox:
    return SecretBox(settings.vk_ads_secret_key.get_secret_value())


async def _get_active(
    session: AsyncSession, account_id: int, community_id: str
) -> CommunityToken | None:
    stmt = select(CommunityToken).where(
        CommunityToken.account_id == account_id,
        CommunityToken.community_id == community_id,
        CommunityToken.status == ACTIVE,
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def save_community_token(
    session: AsyncSession,
    account_id: int,
    community_id: str,
    token: str,
    *,
    screen_name: str,
    community_name: str,
    settings: Settings | None = None,
) -> CommunityTokenInfo:
    """Привязать токен к сообществу.

    `community_id`/`screen_name`/`community_name` вызывающая сторона получает
    ДО этого вызова живым запросом `groups.getById`
    (`integrations/vk_community.py::fetch_own_community`,
    `core/api/v1/senler.py`) — здесь их только сохраняем, руками не вводим и
    не подсматриваем в бриф.

    Второй токен того же сообщества заменяет первый: прежняя активная строка
    архивируется, новая становится активной — без этого частичный уникальный
    индекс (`uq_community_token_active`) отклонил бы вставку.

    Бросает `NotConfiguredError` (`services.secret_box`), если ключ шифрования
    не задан — сохранять секрет незашифрованным нельзя. Коммит — на вызывающем.
    """
    cfg = settings or get_settings()
    box = _box(cfg)  # box.encrypt() сам бросит NotConfiguredError без ключа

    existing = await _get_active(session, account_id, community_id)
    if existing is not None:
        existing.status = ARCHIVED
        await session.flush()

    row = CommunityToken(
        account_id=account_id,
        community_id=community_id,
        screen_name=screen_name.strip().lower(),
        community_name=community_name.strip(),
        token_encrypted=box.encrypt(token.strip()),
        status=ACTIVE,
    )
    session.add(row)
    await session.flush()
    logger.info("community token saved: community_id=%s", community_id)
    return CommunityTokenInfo(
        id=row.id,
        community_id=row.community_id,
        screen_name=row.screen_name,
        community_name=row.community_name,
        created_at=row.created_at,
    )


async def get_decrypted_token(
    session: AsyncSession,
    account_id: int,
    community_id: str,
    *,
    settings: Settings | None = None,
) -> str | None:
    """Расшифрованный токен сообщества по точному числовому id.

    `None` — токена нет, ключ шифрования не настроен, или сохранённый шифротекст
    не расшифровывается (сменился ключ): во всех случаях считаем, что проверить
    подключение нечем, а не имитируем ответ (см. `services/launch_service.py`).
    """
    cfg = settings or get_settings()
    row = await _get_active(session, account_id, community_id)
    if row is None:
        return None
    box = _box(cfg)
    if not box.configured:
        return None
    try:
        return box.decrypt(row.token_encrypted)
    except Exception:  # noqa: BLE001 — сменился ключ шифрования, токен нечитаем
        logger.warning("cannot decrypt community token for community_id=%s", community_id)
        return None


async def find_decrypted_token(
    session: AsyncSession,
    account_id: int,
    *,
    community_id: str | None,
    screen_name: str | None,
    settings: Settings | None = None,
) -> CommunityTokenMatch | None:
    """Найти активный токен сообщества по числовому id ИЛИ короткому адресу.

    Ссылка на сообщество из брифа почти всегда короткий адрес
    (`vk.ru/djbeauty`), а не числовой id — этот поиск сопоставляет сообщество с
    сохранённым токеном по любому признаку, который у неё нашёлся (оба сразу —
    тоже ОК, ищем по любому совпавшему). Сравнение по `screen_name` без учёта
    регистра — так же, как он и сохраняется.

    `None` — оба признака пусты, ни один токен не подошёл, ключ шифрования не
    настроен, либо сохранённый шифротекст не расшифровывается: считаем, что
    проверить подключение нечем (см. `services/launch_service.py`).
    """
    conditions = []
    if community_id:
        conditions.append(CommunityToken.community_id == community_id)
    if screen_name:
        conditions.append(CommunityToken.screen_name == screen_name.strip().lower())
    if not conditions:
        return None

    stmt = select(CommunityToken).where(
        CommunityToken.account_id == account_id,
        CommunityToken.status == ACTIVE,
        or_(*conditions),
    )
    row = (await session.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None

    cfg = settings or get_settings()
    box = _box(cfg)
    if not box.configured:
        return None
    try:
        token = box.decrypt(row.token_encrypted)
    except Exception:  # noqa: BLE001 — сменился ключ шифрования, токен нечитаем
        logger.warning("cannot decrypt community token for community_id=%s", row.community_id)
        return None
    return CommunityTokenMatch(token=token, community_id=row.community_id)


async def delete_community_token(session: AsyncSession, account_id: int, community_id: str) -> bool:
    """Отвязать токен сообщества (мягко, статус `archived`). `True` — что-то убрали."""
    row = await _get_active(session, account_id, community_id)
    if row is None:
        return False
    row.status = ARCHIVED
    await session.flush()
    return True


__all__ = [
    "ACTIVE",
    "ARCHIVED",
    "CommunityTokenInfo",
    "CommunityTokenMatch",
    "delete_community_token",
    "find_decrypted_token",
    "get_decrypted_token",
    "save_community_token",
]
