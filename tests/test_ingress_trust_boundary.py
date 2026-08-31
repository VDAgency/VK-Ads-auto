"""Граница доверия на входе: снаружи ядро доступно ТОЛЬКО через Caddy.

Проверено на живом проде 2026-07-25: порт 8000 публиковался на всех интерфейсах,
и `GET /api/v1/invites` (контакты и имена всех клиентов) отвечал 200 из публичного
интернета — в обход правил `infra/Caddyfile`, которые отдают эти пути как 404 ради
152-ФЗ. Тем же путём был открыт `POST /api/v1/invites`: неавторизованная рассылка
писем и Telegram-сообщений от имени оператора.

Обе половины фикса — конфигурация запуска, а не код, поэтому из работающего
приложения их не проверить. Здесь они закрепляются как инвариант, чтобы правку
нельзя было потерять при следующем редактировании compose или Dockerfile.

Аудит безопасности 2026-09-01 добавил сюда три группы проверок:
  * периметр стал белым списком (было — чёрным, то есть fail-open: новый
    операторский эндпоинт публиковался сам собой). Список публичных путей
    сверяется со схемой приложения, поэтому новый роут попадает под проверку
    без правки теста;
  * схема OpenAPI и `/docs` закрыты на проде — до аудита они отдавались наружу
    и раскрывали все 52 эндпоинта с параметрами и телами запросов;
  * заголовки безопасности (HSTS, CSP, nosniff, DENY, Referrer-Policy) —
    живой прод не отдавал ни одного.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
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


def _caddy_matcher_patterns(name: str) -> list[str]:
    """Достать шаблоны путей у именованного матчера Caddy (`@name path a b c`)."""
    caddyfile = (_ROOT / "infra" / "Caddyfile").read_text(encoding="utf-8")
    line = next(
        (row for row in caddyfile.splitlines() if f"@{name}" in row and " path " in row),
        "",
    )
    assert line, f"В infra/Caddyfile нет матчера @{name} — периметр переписан, тест устарел."
    return line.split(" path ", 1)[1].split()


def _caddy_path_matches(pattern: str, path: str) -> bool:
    """Повторить семантику матчера `path` у Caddy: регистронезависимо, `*` в хвосте."""
    left, right = pattern.lower(), path.lower()
    return right.startswith(left[:-1]) if left.endswith("*") else right == left


def _is_public_by_design(path: str) -> bool:
    """Три вещи, которые обязаны быть доступны из интернета, и ничего сверх них.

    `POST /api/v1/briefs` — приём брифа из веб-формы (у него свой rate-limit);
    `/api/v1/cabinet*` — клиентский кабинет (magic-link и session-cookie);
    `/api/v1/admin/*` — веб-админка оператора (зависимость `require_admin`).
    """
    return (
        path == "/api/v1/briefs"
        or path == "/api/v1/cabinet"
        or path.startswith("/api/v1/cabinet/")
        or path.startswith("/api/v1/admin/")
    )


def _app_api_paths() -> list[str]:
    """Все пути ядра под /api/v1 — берём из схемы, а не из списка в тесте.

    Смысл именно в этом: список роутов растёт сам, и новый эндпоинт попадает под
    проверку без правки теста.
    """
    from core.app import create_app

    schema_paths = create_app().openapi()["paths"]
    return [path for path in schema_paths if path.startswith("/api/v1")]


def test_public_allowlist_opens_exactly_the_public_surface() -> None:
    """Белый список Caddy обязан впускать ровно публичные пути — и ни одного лишнего.

    Аудит 2026-09-01: раньше правило было обратным — перечислялись ЗАКРЫТЫЕ пути,
    а всё прочее проксировалось. Схема fail-open: новый операторский эндпоинт
    становился публичным по умолчанию, и заметить это можно было только вручную
    сверив два списка. Теперь забывчивость приводит к недоступности, а не к утечке.
    """
    patterns = _caddy_matcher_patterns("public_api")

    for path in _app_api_paths():
        # Параметры пути подставляем правдоподобным значением: матчер Caddy
        # работает с конкретным URL, а не с шаблоном FastAPI.
        concrete = path.replace("{brief_id}", "1").replace("{client_id}", "1")
        concrete = concrete.replace("{ad_account_id}", "1").replace("{cabinet_id}", "1")
        concrete = concrete.replace("{campaign_id}", "1")
        opened = any(_caddy_path_matches(pattern, concrete) for pattern in patterns)

        if _is_public_by_design(path):
            assert opened, f"Публичный путь {path} закрыт периметром — сайт не заработает."
        else:
            assert not opened, (
                f"Операторский путь {path} открыт наружу белым списком Caddy. "
                "Такие пути ядро отдаёт без собственной авторизации — бот ходит к ним "
                "внутри compose-сети (CLAUDE.md §1.3, 152-ФЗ)."
            )


def test_unknown_api_path_is_closed_by_default() -> None:
    """Суть фикса: эндпоинт, о котором периметр не знает, закрыт, а не открыт."""
    patterns = _caddy_matcher_patterns("public_api")
    invented = "/api/v1/some-endpoint-added-tomorrow"

    assert not any(_caddy_path_matches(pattern, invented) for pattern in patterns), (
        "Незнакомый путь под /api/v1 обязан быть закрыт по умолчанию: "
        "иначе следующий операторский эндпоинт снова уедет в публичный доступ."
    )


def test_operator_paths_stay_closed() -> None:
    """Явный список того, что уже утекало или раскрывает секреты, — под замком."""
    patterns = _caddy_matcher_patterns("public_api")
    sensitive = [
        "/api/v1/invites",  # контакты и имена клиентов (утечка 2026-07-25)
        "/api/v1/ad-accounts",  # токены доступа к рекламным кабинетам
        "/api/v1/ad-accounts/1",
        "/api/v1/senler/community-token",  # токен доступа к сообществу
        "/api/v1/briefs/1",  # карточка брифа с ПДн клиента
        "/api/v1/cabinets",
        "/api/v1/campaigns/1/stop",
        "/api/v1/stats/sync",
        "/api/v1/ping",
    ]

    for path in sensitive:
        assert not any(_caddy_path_matches(pattern, path) for pattern in patterns), (
            f"{path} обязан отдавать 404 из интернета."
        )


def test_api_docs_are_disabled_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Схема OpenAPI не должна отдаваться наружу.

    Аудит 2026-09-01: на проде `/docs`, `/redoc` и `/openapi.json` отвечали 200 и
    раскрывали все 52 эндпоинта с параметрами и телами запросов — включая
    операторские, которые сами по себе закрыты периметром. Готовая карта API.
    """
    from config.settings import get_settings
    from core.app import create_app

    monkeypatch.setattr(get_settings(), "app_env", "production")
    paths = {getattr(route, "path", None) for route in create_app().routes}

    assert "/docs" not in paths
    assert "/redoc" not in paths
    assert "/openapi.json" not in paths


def test_api_docs_stay_available_outside_production() -> None:
    """Локально документация нужна — запрет привязан именно к продакшену."""
    from config.settings import get_settings
    from core.app import create_app

    assert get_settings().app_env != "production", "тест рассчитан на не-продовый APP_ENV"
    paths = {getattr(route, "path", None) for route in create_app().routes}

    assert "/docs" in paths
    assert "/openapi.json" in paths


def test_caddy_blocks_api_docs_as_second_line() -> None:
    """Второй рубеж на случай, если APP_ENV на сервере окажется не `production`."""
    patterns = _caddy_matcher_patterns("api_docs")

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert any(_caddy_path_matches(pattern, path) for pattern in patterns), (
            f"{path} обязан закрываться и на уровне Caddy."
        )


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


def test_security_headers_are_configured() -> None:
    """Заголовки безопасности отдаёт Caddy — из приложения их не проверить.

    Аудит 2026-09-01: живой прод не отдавал ни одного из них. Здесь они
    закрепляются как инвариант, чтобы правку не потеряли при следующем
    редактировании конфига.
    """
    caddyfile = (_ROOT / "infra" / "Caddyfile").read_text(encoding="utf-8")

    required = {
        "Strict-Transport-Security": "браузер обязан ходить только по HTTPS",
        "X-Content-Type-Options": "загруженный креатив не должен «стать» html",
        "X-Frame-Options": "кабинет открывается по ссылке с токеном — не для фреймов",
        "Referrer-Policy": "токен кабинета живёт в query и не должен утекать в Referer",
        "Content-Security-Policy": "внешних ресурсов у страниц нет — запрещаем их вовсе",
        "Permissions-Policy": "камера/микрофон/геолокация сайту не нужны",
    }
    for header, why in required.items():
        assert header in caddyfile, f"Пропал заголовок {header}: {why}."

    assert "frame-ancestors 'none'" in caddyfile, "CSP обязан запрещать обрамление страницы."
    assert "-Server" in caddyfile, "Заголовок Server раскрывал, что внутри uvicorn."
