"""Контейнеры не работают от root (аудит 2026-09-01, CKV_DOCKER_3).

Собрать образ локально нельзя — демон Docker на машине разработки не поднимается.
Поэтому инварианты закрепляются по содержимому файлов, а фактическая работа
проверяется на проде после выкатки.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]  # PyYAML без стабов; ставить types-PyYAML не нужно — есть транзитивно, не отдельная зависимость проекта.

_ROOT = Path(__file__).resolve().parent.parent
_APP_IMAGES = ("Dockerfile", "Dockerfile.userbot", "Dockerfile.kotbot")
_SERVICES_NEEDING_PERMS = ("api", "bot", "userbot", "kotbot", "backup")


def test_app_images_declare_non_root_user() -> None:
    for name in _APP_IMAGES:
        text = (_ROOT / name).read_text(encoding="utf-8")
        users = [
            line.split(maxsplit=1)[1].strip()
            for line in text.splitlines()
            if line.startswith("USER ")
        ]
        assert users, f"{name}: нет инструкции USER — контейнер работает от root."
        assert users[-1] != "root", f"{name}: последний USER — root."


def test_backup_image_runs_as_postgres() -> None:
    text = (_ROOT / "infra" / "backup" / "Dockerfile").read_text(encoding="utf-8")
    assert "USER postgres" in text


def test_kotbot_keeps_browsers_outside_root_home() -> None:
    """Playwright по умолчанию ставит браузер в /root/.cache (права 0700).

    Под непривилегированным пользователем такой браузер не запустится, и kotbot
    молча перестанет работать.
    """
    text = (_ROOT / "Dockerfile.kotbot").read_text(encoding="utf-8")
    assert "PLAYWRIGHT_BROWSERS_PATH" in text
    assert "/root" not in text.split("PLAYWRIGHT_BROWSERS_PATH")[1].splitlines()[0]


def _compose() -> dict[str, Any]:
    result: dict[str, Any] = yaml.safe_load(
        (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    return result


def test_runtime_commands_do_not_resync() -> None:
    """`uv run` без --no-sync пишет в окружение проекта и падает без прав.

    Проверяем не только CMD/ENTRYPOINT в самих Dockerfile'ах, но и переопределения
    `command` в docker-compose.yml — они имеют приоритет над CMD образа, и именно
    такое переопределение (сервис `bot`) один раз проехало мимо этой проверки.
    """
    for name in _APP_IMAGES:
        text = (_ROOT / name).read_text(encoding="utf-8")
        cmd = [line for line in text.splitlines() if line.startswith(("CMD", "ENTRYPOINT"))]
        joined = " ".join(cmd)
        if "uv" in joined and "run" in joined:
            assert "--no-sync" in joined, f"{name}: рантайм-команда пересинхронизирует окружение."

    services = _compose()["services"]
    for name, service in services.items():
        command = service.get("command")
        if command is None:
            continue
        joined_command = (
            " ".join(str(part) for part in command) if isinstance(command, list) else str(command)
        )
        if "uv" in joined_command and "run" in joined_command:
            assert "--no-sync" in joined_command, (
                f"docker-compose.yml: сервис {name} переопределяет command и "
                "пересинхронизирует окружение при старте."
            )


def test_permission_init_service_exists_and_is_one_shot() -> None:
    services = _compose()["services"]
    assert "init-permissions" in services, "нет сервиса подготовки прав на томах"
    init = services["init-permissions"]
    assert init.get("restart", "no") == "no", (
        "init-permissions завершается — с restart: unless-stopped Docker будет "
        "перезапускать его бесконечно."
    )


def test_services_wait_for_permissions() -> None:
    services = _compose()["services"]
    for name in _SERVICES_NEEDING_PERMS:
        depends = services[name].get("depends_on") or {}
        assert isinstance(depends, dict), (
            f"{name}: depends_on должен быть в развёрнутой форме — короткая не умеет "
            "условие service_completed_successfully."
        )
        assert depends.get("init-permissions", {}).get("condition") == (
            "service_completed_successfully"
        ), f"{name}: не ждёт подготовки прав, стартует раньше и падает по правам."
