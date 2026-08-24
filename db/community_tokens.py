"""Хранилище токена сообщества VK для проверки подключения Senler (B2).

Функции доступа для `db/models.py::CommunityToken` — по образцу `AdAccount`
(`db/repositories.py` + `services/ad_accounts.py`), но собраны в одном узком
модуле: обработчик бота (`bot/handlers/senler.py`) сюда не ходит напрямую —
только через тонкий эндпоинт ядра (CLAUDE.md §1.3), а сам эндпоинт зовёт эти
функции.

Шифрование — Fernet (`services.secret_box`), тем же ключом (`VK_ADS_SECRET_KEY`),
что и токены рекламных кабинетов. Расшифрованный токен наружу не возвращается
нигде, кроме `get_decrypted_token` — им пользуется только проверка подключения
перед запуском (`services/launch_service.py`); результат в БД и логи не попадает.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from config.settings import Settings, get_settings
from services.secret_box import SecretBox
from sqlalchemy import select
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
    created_at: datetime


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
    settings: Settings | None = None,
) -> CommunityTokenInfo:
    """Привязать токен к сообществу.

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
        token_encrypted=box.encrypt(token.strip()),
        status=ACTIVE,
    )
    session.add(row)
    await session.flush()
    logger.info("community token saved: community_id=%s", community_id)
    return CommunityTokenInfo(id=row.id, community_id=row.community_id, created_at=row.created_at)


async def get_decrypted_token(
    session: AsyncSession,
    account_id: int,
    community_id: str,
    *,
    settings: Settings | None = None,
) -> str | None:
    """Расшифрованный токен сообщества — только для проверки подключения перед запуском.

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
    "delete_community_token",
    "get_decrypted_token",
    "save_community_token",
]
