"""Проверка достижимости точек подключения — та самая матрица, но изнутри контейнера.

Раньше её приходилось снимать вручную по SSH; теперь оператор видит то же самое
командой в боте. Проверяется только TCP: этого достаточно, чтобы отличить «адрес
закрыт» от «адрес открыт, но обмен MTProto режется» — а именно это различие и
оказалось ключевым при разборе блокировки.

Пробник инъектируется, поэтому тесты обходятся без сокетов.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from userbot.endpoints import Endpoint

# (хост, порт, предел ожидания) → задержка в секундах или None, если не отозвался.
Probe = Callable[[str, int, float], Awaitable[float | None]]


@dataclass(frozen=True, slots=True)
class EndpointProbe:
    """Результат проверки одной точки."""

    endpoint: Endpoint
    latency: float | None

    @property
    def reachable(self) -> bool:
        return self.latency is not None


async def tcp_probe(host: str, port: int, limit: float) -> float | None:
    """Открыть TCP-соединение и сразу закрыть. `None` — не отозвался."""
    started = time.monotonic()
    writer = None
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=limit)
        return time.monotonic() - started
    except (TimeoutError, OSError):
        return None
    finally:
        if writer is not None:
            writer.close()


async def probe_endpoints(
    endpoints: list[Endpoint],
    *,
    probe: Probe = tcp_probe,
    limit: float = 3.0,
    budget: float = 15.0,
) -> list[EndpointProbe]:
    """Проверить точки параллельно, уложившись в общий бюджет.

    Параллельно — потому что последовательный обход десятка мёртвых адресов занял бы
    полминуты, а экран диагностики должен открываться сразу.
    """

    async def one(endpoint: Endpoint) -> EndpointProbe:
        latency = await probe(endpoint.ip, endpoint.port, limit)
        return EndpointProbe(endpoint=endpoint, latency=latency)

    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(one(endpoint) for endpoint in endpoints)), timeout=budget
        )
    except TimeoutError:
        # Бюджет исчерпан — отдаём то, что успели бы отдать: все как недоступные.
        return [EndpointProbe(endpoint=endpoint, latency=None) for endpoint in endpoints]
    return list(results)
