"""Мок-гейт (§7 spec 2026-07-15 + задача 2 дефект 2): показывать демо-данные.

Моки не пишутся в БД — синтезируются на лету, только пока гейт открыт. «Автоудаление»
из требований = автозакрытие гейта: как только появились реальные данные, истёк срок
или набран порог клиентов, моки исчезают сами (чистить нечего).

Гейт закрыт по умолчанию: без явного `MOCK_STATS_ENABLED=true` демо не показываются,
что бы ни говорили остальные условия — иначе демо-кабинеты всплывают сами по себе
(например, на приёмке у заказчика), а отличие от реальных данных — только баннер.
"""

from __future__ import annotations

from datetime import UTC, date, datetime


def _parse_until(raw: str) -> datetime:
    """Разобрать `MOCK_UNTIL` (ISO-дата) в конец этого дня по UTC."""
    parsed = date.fromisoformat(raw)
    return datetime(parsed.year, parsed.month, parsed.day, 23, 59, 59, tzinfo=UTC)


def mock_enabled(
    *,
    real_count: int,
    clients_count: int,
    mock_until: str,
    mock_max_clients: int,
    mock_stats_enabled: bool,
    now: datetime | None = None,
) -> bool:
    """Гейт открыт (моки показываем), только если явный флаг включён И выполнены
    ВСЕ условия §7.

    0. `mock_stats_enabled` — явный флаг окружения (`MOCK_STATS_ENABLED`); без него
       гейт закрыт всегда, остальные условия не проверяются (дефект 2 — демо не
       должны быть видны по умолчанию);
    1. `real_count == 0` — реальных данных ещё нет;
    2. `now < MOCK_UNTIL` — не истёк срок демо;
    3. `clients_count < MOCK_MAX_CLIENTS` — клиентов меньше порога.
    """
    if not mock_stats_enabled:
        return False
    now = now or datetime.now(UTC)
    if real_count > 0:
        return False
    if now >= _parse_until(mock_until):
        return False
    return clients_count < mock_max_clients
