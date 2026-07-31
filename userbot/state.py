"""Состояние сессий операторов в памяти (spec 2026-07-31 §4.5).

Зачем отдельный реестр. Раньше «состояние сессии» вычислялось прямо в `/health` —
то есть каждый опрос лез в сеть и подключался. При недоступном дата-центре один
запрос занимал полминуты и больше, healthcheck контейнера не укладывался в свой
таймаут, а поллер бота считал недоступным весь сервис. Теперь состояние живёт в
памяти, обновляет его фоновая проверка, а эндпоинты только читают.

Ключевое различие, ради которого заведён набор состояний: `UNREACHABLE` (до Telegram
не достучались — восстановится само) против `EXPIRED` (ключ авторизации мёртв — нужна
перепривязка). Свести их в один флаг значит давать оператору неверный совет.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class SessionState(StrEnum):
    """Состояние сессии оператора."""

    UNKNOWN = "unknown"  # ещё не проверяли
    READY = "ready"  # подключена и авторизована
    UNREACHABLE = "unreachable"  # сеть/дата-центр недоступны, ретраим
    EXPIRED = "expired"  # ключ мёртв, нужна перепривязка
    BANNED = "banned"  # аккаунт заблокирован, ретраить бесполезно
    ABSENT = "absent"  # сессии нет вовсе


# Состояния, из которых сессия сама не выберется: повторные попытки бесполезны, а при
# отозванном ключе ещё и вредны — Telegram агрессивнее реагирует на настойчивость.
TERMINAL_STATES = frozenset({SessionState.EXPIRED, SessionState.BANNED})


@dataclass(slots=True)
class SessionInfo:
    """Что мы знаем о сессии оператора и когда узнали."""

    sender_id: int
    state: SessionState = SessionState.UNKNOWN
    phone: str | None = None
    endpoint: str | None = None
    last_ok_at: float | None = None
    last_error: str | None = None
    last_error_at: float | None = None
    consecutive_failures: int = 0
    # Раньше этого момента следующую проверку не делаем (откат для недоступных).
    next_attempt_at: float = 0.0

    def as_dict(self) -> dict[str, object]:
        """Представление для HTTP-ответа."""
        return {
            "sender_id": self.sender_id,
            "state": self.state.value,
            # `authorized` оставлен для совместимости с существующими клиентами.
            "authorized": self.state is SessionState.READY,
            "phone": self.phone,
            "endpoint": self.endpoint,
            "error": self.last_error,
            "last_ok_at": self.last_ok_at,
        }


@dataclass(slots=True)
class StateRegistry:
    """Реестр состояний по операторам. Всё в памяти: рестарт → проверка наполнит заново."""

    _items: dict[int, SessionInfo] = field(default_factory=dict)

    def get(self, sender_id: int) -> SessionInfo:
        """Состояние оператора; неизвестного заводим как `UNKNOWN`."""
        info = self._items.get(sender_id)
        if info is None:
            info = SessionInfo(sender_id=sender_id)
            self._items[sender_id] = info
        return info

    def snapshot(self) -> list[SessionInfo]:
        """Все известные состояния, по возрастанию sender_id."""
        return [self._items[key] for key in sorted(self._items)]

    def forget(self, sender_id: int) -> None:
        """Убрать оператора из реестра (сессию удалили)."""
        self._items.pop(sender_id, None)

    def mark_ok(
        self, sender_id: int, *, phone: str | None, endpoint: str | None, now: float | None = None
    ) -> SessionInfo:
        """Отметить успешную проверку: сессия жива, счётчик неудач сброшен."""
        moment = time.time() if now is None else now
        info = self.get(sender_id)
        info.state = SessionState.READY
        info.phone = phone
        info.endpoint = endpoint
        info.last_ok_at = moment
        info.last_error = None
        info.last_error_at = None
        info.consecutive_failures = 0
        info.next_attempt_at = 0.0
        return info

    def mark_failed(
        self,
        sender_id: int,
        *,
        state: SessionState,
        error: str | None,
        now: float | None = None,
        backoff_base: float = 300.0,
        backoff_cap: float = 3600.0,
    ) -> SessionInfo:
        """Отметить неудачу и назначить время следующей попытки.

        Откат растёт по степени двойки: мёртвый дата-центр не нужно дёргать каждые
        пять минут. Терминальные состояния не ретраим вовсе.
        """
        moment = time.time() if now is None else now
        info = self.get(sender_id)
        info.state = state
        info.last_error = error
        info.last_error_at = moment
        info.consecutive_failures += 1
        if state in TERMINAL_STATES:
            info.next_attempt_at = float("inf")
        else:
            delay = min(backoff_base * (2 ** (info.consecutive_failures - 1)), backoff_cap)
            info.next_attempt_at = moment + delay
        return info

    def due(self, sender_id: int, now: float) -> bool:
        """Пора ли проверять сессию заново."""
        return now >= self.get(sender_id).next_attempt_at
