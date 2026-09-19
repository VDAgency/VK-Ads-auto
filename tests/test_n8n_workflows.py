"""Страховка для `n8n/workflows/` — репозиторий публичный (CLAUDE.md §1.1).

Воркфлоу n8n — код в git (docs/N8N.md, раздел 1): каждый файл должен быть
валидным JSON, а секреты (креды Senler, API-ключи и т. п.) там появляться не
могут в принципе — n8n хранит их отдельно, зашифрованными в своей БД, и
экспортирует в JSON только ССЫЛКУ на credential по имени. Эти тесты закрепляют
инвариант, чтобы будущая ручная правка воркфлоу не занесла в git настоящий
ключ по ошибке (docs/N8N.md, раздел 2).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_WORKFLOWS_DIR = _ROOT / "n8n" / "workflows"

# Ключи, под которыми в JSON воркфлоу секрет мог бы просочиться. Сами креды
# n8n экспортирует как {"id": ..., "name": ...} — без значения, поэтому
# непустая строка под таким ключом уже подозрительна.
_SECRET_KEY_PATTERN = re.compile(
    r"(api[_-]?key|password|passwd|secret|(?<!access_)token|access[_-]?token|encryption[_-]?key)",
    re.IGNORECASE,
)

# Ключи-исключения: легитимные поля n8n/JSON, которые ложно совпадают с
# паттерном выше (не хранят секрет, а служебные метаданные схемы).
_SAFE_KEY_EXACT = {"webhookId", "authTokenType"}

# Узнаваемые форматы реальных ключей/токенов — на случай, если секрет попал
# в JSON не под "говорящим" ключом (например, вставлен прямо в URL или тело).
_KNOWN_SECRET_VALUE_PATTERNS = [
    re.compile(r"vk1\.[ar]\.[A-Za-z0-9_-]+"),  # VK OAuth access/refresh токен
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"),  # JWT
]


def _workflow_files() -> list[Path]:
    if not _WORKFLOWS_DIR.exists():
        return []
    return sorted(p for p in _WORKFLOWS_DIR.glob("*.json") if p.is_file())


def _iter_secret_like_fields(obj: Any, path: str = "") -> list[tuple[str, str]]:
    """Обходит JSON и возвращает (путь, значение) для подозрительных полей."""
    found: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f"{path}.{key}" if path else str(key)
            if (
                isinstance(value, str)
                and value.strip()
                and key not in _SAFE_KEY_EXACT
                and _SECRET_KEY_PATTERN.search(str(key))
            ):
                found.append((new_path, value))
            found.extend(_iter_secret_like_fields(value, new_path))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            found.extend(_iter_secret_like_fields(item, f"{path}[{index}]"))
    return found


def test_workflows_directory_has_at_least_one_workflow() -> None:
    """Требование договора (Приложение №1, п.2.1): n8n — не просто заявленный
    в docker-compose сервис, а рабочая оркестрация. Пустой каталог = требование
    не выполнено по существу."""
    assert _workflow_files(), (
        "n8n/workflows/ пуст: нет ни одного воркфлоу. n8n должен реально "
        "оркестрировать хотя бы один процесс (docs/N8N.md)."
    )


def test_workflow_files_are_valid_json() -> None:
    files = _workflow_files()
    assert files, "нет файлов воркфлоу для проверки"
    for path in files:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            pytest.fail(f"{path.name}: невалидный JSON — {exc}")


def test_workflow_files_have_no_secret_fields() -> None:
    """В JSON не должно быть непустых полей apiKey/password/token/secret и т.п.

    Креды в n8n вводятся руками в UI и подтягиваются по имени (docs/N8N.md,
    раздел 2) — в экспортированном JSON их значений быть не может."""
    files = _workflow_files()
    assert files, "нет файлов воркфлоу для проверки"
    for path in files:
        data = json.loads(path.read_text(encoding="utf-8"))
        offenders = _iter_secret_like_fields(data)
        assert not offenders, (
            f"{path.name}: похожие на секреты поля найдены в JSON — {offenders}. "
            "Значения кредов не должны попадать в git (CLAUDE.md §1.1)."
        )


def test_workflow_files_have_no_known_secret_value_patterns() -> None:
    """Доп. страховка: секрет мог попасть в файл не под «говорящим» ключом
    (например, вставлен прямо в URL или тело запроса), поэтому дополнительно
    сканируем сырой текст на узнаваемые форматы реальных ключей."""
    files = _workflow_files()
    assert files, "нет файлов воркфлоу для проверки"
    for path in files:
        text = path.read_text(encoding="utf-8")
        for pattern in _KNOWN_SECRET_VALUE_PATTERNS:
            match = pattern.search(text)
            assert not match, f"{path.name}: похоже на реальный ключ — {match.group(0)!r}"


def test_workflow_files_use_internal_service_hostnames() -> None:
    """Обращения к нашему API должны идти по внутреннему имени сервиса
    compose-сети (`api`), а не через публичный домен — воркфлоу работает
    внутри docker-сети, снаружи путь закрыт на ingress (docs/N8N.md)."""
    files = _workflow_files()
    assert files, "нет файлов воркфлоу для проверки"
    forbidden_hosts = ("https://", "vk-ads-auto.ru")
    for path in files:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        urls = [str(value) for _, value in _walk_all_strings(data) if str(value).startswith("http")]
        for url in urls:
            assert not any(host in url for host in forbidden_hosts), (
                f"{path.name}: обращение по публичному адресу ({url}) вместо "
                "внутреннего имени сервиса compose-сети."
            )


def _load_workflow(filename: str) -> dict[str, Any]:
    path = _WORKFLOWS_DIR / filename
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _node(workflow: dict[str, Any], node_type: str) -> dict[str, Any]:
    node: dict[str, Any] = next(n for n in workflow["nodes"] if n["type"] == node_type)
    return node


# --- daily_digest.json: ежедневная сводка оператору (09:00 МСК, задача 3А) ---------


def test_daily_digest_workflow_is_inactive_by_default() -> None:
    """Как и часовой синк — заводится выключенным, оператор включает вручную (docs/N8N.md)."""
    workflow = _load_workflow("daily_digest.json")
    assert workflow["id"] == "daily-digest"
    assert workflow["active"] is False


def test_daily_digest_workflow_runs_at_nine_am_moscow_time() -> None:
    workflow = _load_workflow("daily_digest.json")
    assert workflow["settings"]["timezone"] == "Europe/Moscow"
    assert workflow["settings"]["executionOrder"] == "v1"

    trigger = _node(workflow, "n8n-nodes-base.scheduleTrigger")
    rule = trigger["parameters"]["rule"]["interval"][0]
    assert rule["field"] == "cronExpression"
    assert rule["expression"] == "0 9 * * *"


def test_daily_digest_workflow_posts_to_internal_digest_endpoint() -> None:
    workflow = _load_workflow("daily_digest.json")
    http_node = _node(workflow, "n8n-nodes-base.httpRequest")
    assert http_node["parameters"]["method"] == "POST"
    assert http_node["parameters"]["url"] == "http://api:8000/api/v1/stats/digest"


def test_daily_digest_workflow_needs_no_credentials() -> None:
    """Эндпоинт не авторизован намеренно (изоляция на уровне docker-сети) — узла credentials нет."""
    workflow = _load_workflow("daily_digest.json")
    http_node = _node(workflow, "n8n-nodes-base.httpRequest")
    assert "credentials" not in http_node


def _walk_all_strings(obj: Any, path: str = "") -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            new_path = f"{path}.{key}" if path else str(key)
            if isinstance(value, str):
                found.append((new_path, value))
            found.extend(_walk_all_strings(value, new_path))
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            found.extend(_walk_all_strings(item, f"{path}[{index}]"))
    return found
