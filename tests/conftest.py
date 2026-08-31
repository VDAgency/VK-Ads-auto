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
from config.settings import get_settings
from core.api.rate_limit import _brief_limiter, _cabinet_auth_limiter

# Telegram ID, которым во множестве тестов подделывают admin-сессию/логин
# (`generate_admin_session(555, ...)`) — сложившаяся конвенция тестов, не
# связанная с реальными ID в `.env`.
_FAKE_ADMIN_TELEGRAM_ID = 555


@pytest.fixture(autouse=True)
def _reset_rate_limiters() -> Iterator[None]:
    """Сбросить оба лимитера перед каждым тестом — изоляция от соседних тестов."""
    _brief_limiter.reset()
    _cabinet_auth_limiter.reset()
    yield


@pytest.fixture(autouse=True)
def _allow_fake_admin_operator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Разрешить тестовый Telegram ID 555 как оператора на время теста.

    С spec 2026-08-31 `require_admin` и `/admin/login` сверяются со списком
    `OPERATOR_TELEGRAM_IDS` (config/settings.py), а не только с подписью
    токена/паролем — иначе оператор, убранный из списка, продолжал бы
    заходить по старой сессии или паролю. Настоящий список читается из
    `.env` и тестового ID 555 в нём нет — без этой фикстуры сломались бы все
    тесты, подделывающие admin-сессию через `generate_admin_session(555, ...)`.
    Тест, который проверяет именно отзыв доступа, сам сужает список обратно
    (`monkeypatch.setattr(get_settings(), "operator_telegram_ids", ...)`) —
    это применится поверх данной фикстуры и откатится вместе с ней.
    """
    settings = get_settings()
    monkeypatch.setattr(
        settings,
        "operator_telegram_ids",
        settings.operator_telegram_ids | {_FAKE_ADMIN_TELEGRAM_ID},
    )
