"""Простой in-memory rate-limit по IP (spec §7.2: POST /briefs — 30 rpm).

Скользящее окно на процесс: словарь `ip -> список меток времени` за последнее
окно. Достаточно для MVP (один инстанс API). При масштабировании на несколько
воркеров вынести в Redis. Не тащим внешних зависимостей.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindowRateLimiter:
    """Ограничитель «не более `limit` событий за `window_seconds` с одного ключа»."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self._limit = limit
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, now: float | None = None) -> bool:
        """Зарегистрировать попытку и сказать, разрешена ли она."""
        current = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._hits[key]
            cutoff = current - self._window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self._limit:
                return False
            bucket.append(current)
            return True
