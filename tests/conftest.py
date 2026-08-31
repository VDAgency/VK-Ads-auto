"""Общие фикстуры pytest.

`core.api.rate_limit` держит лимитеры как модульные синглтоны (скользящее окно
в памяти процесса, см. докстринг модуля). Ключ — IP клиента; и `TestClient`,
и `httpx.AsyncClient` с `ASGITransport` подставляют один и тот же фиксированный
IP, поэтому без сброса тесты из разных файлов, дергающие один и тот же
rate-limited эндпоинт (`/admin/login`, `/cabinet/login`, ...), делят общий
счётчик попыток и могут словить неожиданный 429 из-за чужих вызовов, сделанных
раньше в этом же процессе pytest.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from core.api.rate_limit import _brief_limiter, _cabinet_auth_limiter


@pytest.fixture(autouse=True)
def _reset_rate_limiters() -> Iterator[None]:
    """Сбросить оба лимитера перед каждым тестом — изоляция от соседних тестов."""
    _brief_limiter.reset()
    _cabinet_auth_limiter.reset()
    yield
