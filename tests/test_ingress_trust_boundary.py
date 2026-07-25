"""Граница доверия на входе: снаружи ядро доступно ТОЛЬКО через Caddy.

Проверено на живом проде 2026-07-25: порт 8000 публиковался на всех интерфейсах,
и `GET /api/v1/invites` (контакты и имена всех клиентов) отвечал 200 из публичного
интернета — в обход правил `infra/Caddyfile`, которые отдают эти пути как 404 ради
152-ФЗ. Тем же путём был открыт `POST /api/v1/invites`: неавторизованная рассылка
писем и Telegram-сообщений от имени оператора.

Обе половины фикса — конфигурация запуска, а не код, поэтому из работающего
приложения их не проверить. Здесь они закрепляются как инвариант, чтобы правку
нельзя было потерять при следующем редактировании compose или Dockerfile.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from core.api.rate_limit import cabinet_auth_rate_limit
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.types import ASGIApp
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

_ROOT = Path(__file__).resolve().parent.parent


def test_compose_publishes_api_only_on_loopback() -> None:
    compose = (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert '"127.0.0.1:8000:8000"' in compose, (
        "Порт ядра должен публиковаться только на loopback: публикация на всех "
        "интерфейсах открывает операторские эндпоинты мимо Caddy (утечка ПДн, 152-ФЗ)."
    )
    assert '- "8000:8000"' not in compose, "Осталась публикация порта 8000 на всех интерфейсах."


def test_dockerfile_runs_uvicorn_behind_proxy_headers() -> None:
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "--proxy-headers" in dockerfile, (
        "Без --proxy-headers uvicorn видит IP Caddy, а не клиента: rate-limit "
        "становится общим на всех посетителей сразу (ложные 429)."
    )
    assert "--forwarded-allow-ips" in dockerfile, (
        "uvicorn игнорирует X-Forwarded-For, пока источник не объявлен доверенным."
    )


def _probe_app() -> ASGIApp:
    """Минимальное приложение с настоящей зависимостью rate-limit, как в проде."""
    app = FastAPI()

    @app.get("/probe", dependencies=[Depends(cabinet_auth_rate_limit)])
    def probe(request: Request) -> dict[str, str]:
        return {"client": request.client.host if request.client else ""}

    # uvicorn и starlette описывают один и тот же ASGI разными типами (в рантайме
    # совместимы) — отсюда cast на обоих стыках, иначе mypy strict не пропускает.
    return cast(ASGIApp, ProxyHeadersMiddleware(cast(Any, app), trusted_hosts="*"))


def test_forwarded_for_becomes_client_host() -> None:
    with TestClient(_probe_app()) as client:
        resp = client.get("/probe", headers={"X-Forwarded-For": "203.0.113.1"})
    assert resp.status_code == 200
    assert resp.json()["client"] == "203.0.113.1"


def test_rate_limit_buckets_are_per_forwarded_client() -> None:
    # Лимит кабинета — 10 запросов в минуту на ключ. Исчерпываем его для одного
    # клиента и убеждаемся, что второй не задет. IP уникальны для этого теста:
    # лимитер — модульный синглтон, бакеты живут между тестами.
    with TestClient(_probe_app()) as client:
        noisy = [
            client.get("/probe", headers={"X-Forwarded-For": "203.0.113.21"}).status_code
            for _ in range(12)
        ]
        quiet = client.get("/probe", headers={"X-Forwarded-For": "203.0.113.22"}).status_code

    assert 429 in noisy, "лимит шумного клиента обязан сработать"
    assert quiet == 200, "тихий клиент не должен страдать от чужого лимита"
