"""Лёгкий rate-limit для публичных эндпоинтов (напр. приём брифа).

Скользящее окно в памяти процесса: достаточно для single-instance деплоя ядра
(один контейнер API). При переходе на несколько реплик — заменить на Redis
(инкремент с TTL). Ключ — IP клиента; при отсутствии IP лимит не применяется.
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class SlidingWindowLimiter:
    """Ограничитель «N запросов за окно секунд» по ключу (обычно IP)."""

    def __init__(self, max_requests: int, window_seconds: float) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, now: float | None = None) -> bool:
        """True — запрос разрешён (и учтён); False — лимит превышен."""
        current = time.monotonic() if now is None else now
        hits = self._hits[key]
        threshold = current - self._window
        while hits and hits[0] <= threshold:
            hits.popleft()
        if len(hits) >= self._max:
            return False
        hits.append(current)
        return True


# 30 запросов в минуту на IP — приём брифа (см. plan PR#4).
_brief_limiter = SlidingWindowLimiter(max_requests=30, window_seconds=60.0)


def brief_rate_limit(request: Request) -> None:
    """FastAPI-зависимость: 429, если IP превысил лимит приёма брифа."""
    client = request.client
    if client is None:  # TestClient без host — не лимитируем
        return
    if not _brief_limiter.check(client.host):
        raise HTTPException(status_code=429, detail="Слишком много запросов, попробуйте позже.")
