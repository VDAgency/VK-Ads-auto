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
from collections.abc import Sequence
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
    """Активный токен по точному числовому id.

    Числовой id не переиспользуется VK (в отличие от `screen_name`, который
    может достаться другому сообществу — см. `_get_active_by_screen_name`), а
    частичный уникальный индекс `uq_community_token_active` не даёт завестись
    второй активной строке с тем же id. `.limit(1)` здесь — защита в глубину на
    случай аномалии данных, а не спасение от штатной неоднозначности.
    """
    stmt = (
        select(CommunityToken)
        .where(
            CommunityToken.account_id == account_id,
            CommunityToken.community_id == community_id,
            CommunityToken.status == ACTIVE,
        )
        .order_by(CommunityToken.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _get_active_by_screen_name(
    session: AsyncSession, account_id: int, screen_name: str
) -> CommunityToken | None:
    """Активный токен по короткому адресу — без гарантии уникальности от БД.

    Адрес сообщества можно переиспользовать (сообщество сменило адрес, старый
    достался другому), поэтому здесь, в отличие от `_get_active`, `.limit(1)`
    обязателен, а не просто подстраховка: без него тот же запрос на аномальных
    данных (две активные строки с одним адресом) роняет `scalar_one_or_none()`
    необработанным `MultipleResultsFound` (ревью 2026-08-24, дефект 1 —
    воспроизведён вживую). Явный порядок делает выбор предсказуемым: при
    аномалии выигрывает самая свежая привязка — если адрес перешёл к другому
    сообществу, для проверки Senler актуальна именно она, а не старая.
    """
    stmt = (
        select(CommunityToken)
        .where(
            CommunityToken.account_id == account_id,
            CommunityToken.screen_name == screen_name,
            CommunityToken.status == ACTIVE,
        )
        .order_by(CommunityToken.id.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def _list_active_by_screen_name(
    session: AsyncSession, account_id: int, screen_name: str
) -> Sequence[CommunityToken]:
    """Все активные строки с этим адресом — используется только при сохранении
    нового токена (`save_community_token`), чтобы архивировать ВСЕ конфликтующие
    привязки, а не одну: аномалия из старых данных не должна дожить ещё один
    цикл сохранения."""
    stmt = select(CommunityToken).where(
        CommunityToken.account_id == account_id,
        CommunityToken.screen_name == screen_name,
        CommunityToken.status == ACTIVE,
    )
    return (await session.execute(stmt)).scalars().all()


async def _find_active_row(
    session: AsyncSession,
    account_id: int,
    *,
    community_id: str | None,
    screen_name: str | None,
) -> CommunityToken | None:
    """Общий поиск активной строки по числовому id ИЛИ короткому адресу —
    числовой id приоритетнее (см. `_get_active_by_screen_name`): проверяем его
    первым и, если он совпал, короткий адрес уже не смотрим вовсе. Используется
    и поиском токена под запуск (`find_decrypted_token`), и отвязкой
    (`delete_community_token`) — один и тот же приём, без секрета.
    """
    row: CommunityToken | None = None
    if community_id:
        row = await _get_active(session, account_id, community_id)
    if row is None and screen_name:
        row = await _get_active_by_screen_name(session, account_id, screen_name.strip().lower())
    return row


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

    Короткий адрес архивируется отдельно и ВСЕГДА, даже если его держит другая
    строка (другой `community_id`): сообщество могло сменить адрес, и тот
    достался другому клиенту (ревью 2026-08-24, дефект 1). Без этого шага в
    базе остаются два активных токена на один адрес и поиск по нему
    (`find_decrypted_token`) становится неоднозначным — так этот класс
    аномалий не допускается вовсе, а не только терпится поиском задним числом.

    Бросает `NotConfiguredError` (`services.secret_box`), если ключ шифрования
    не задан — сохранять секрет незашифрованным нельзя. Коммит — на вызывающем.
    """
    cfg = settings or get_settings()
    box = _box(cfg)  # box.encrypt() сам бросит NotConfiguredError без ключа

    screen = screen_name.strip().lower()
    archived_ids: set[int] = set()

    existing_by_id = await _get_active(session, account_id, community_id)
    if existing_by_id is not None:
        existing_by_id.status = ARCHIVED
        archived_ids.add(existing_by_id.id)

    for row_conflicting in await _list_active_by_screen_name(session, account_id, screen):
        if row_conflicting.id not in archived_ids:
            row_conflicting.status = ARCHIVED
            archived_ids.add(row_conflicting.id)

    if archived_ids:
        await session.flush()

    row = CommunityToken(
        account_id=account_id,
        community_id=community_id,
        screen_name=screen,
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
    сохранённым токеном по любому признаку, который у неё нашёлся. Приоритет —
    у числового id: он не переиспользуется VK, а короткий адрес может достаться
    другому сообществу (ревью 2026-08-24, дефект 1) — если id совпал, короткий
    адрес уже не смотрим (см. `_find_active_row`). Сравнение по `screen_name`
    без учёта регистра — так же, как он и сохраняется.

    `None` — оба признака пусты, ни один токен не подошёл, ключ шифрования не
    настроен, либо сохранённый шифротекст не расшифровывается: считаем, что
    проверить подключение нечем (см. `services/launch_service.py`).
    """
    row = await _find_active_row(
        session, account_id, community_id=community_id, screen_name=screen_name
    )
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


async def delete_community_token(
    session: AsyncSession,
    account_id: int,
    *,
    community_id: str | None,
    screen_name: str | None,
) -> CommunityTokenInfo | None:
    """Отвязать активный токен сообщества (мягко, статус `archived`).

    Ищет по числовому id ИЛИ короткому адресу — тем же приёмом и приоритетом,
    что и поиск токена под запуск (`find_decrypted_token`,
    `_find_active_row`): оператор обычно знает адрес сообщества, а не его
    числовой id (ревью 2026-08-24, дефект 3 — раньше функция существовала, но
    её никто не вызывал, снять устаревшую привязку можно было только правкой
    базы руками).

    Возвращает метаданные отвязанной строки (без секрета) — понятный ответ
    оператору о том, ЧТО именно отвязано; `None` — подходящей активной
    привязки не было.
    """
    row = await _find_active_row(
        session, account_id, community_id=community_id, screen_name=screen_name
    )
    if row is None:
        return None
    row.status = ARCHIVED
    await session.flush()
    return CommunityTokenInfo(
        id=row.id,
        community_id=row.community_id,
        screen_name=row.screen_name,
        community_name=row.community_name,
        created_at=row.created_at,
    )


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
