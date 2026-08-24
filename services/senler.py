"""Проверка «подключён ли Senler» по callback-серверам сообщества VK.

Разведка 2026-08-24 (живой запрос `groups.getCallbackServers` к сообществу с
подключённым чат-ботом Senler):

    {"response": {"count": 1, "items": [
        {"id": 2, "title": "Senler", "creator_id": -228817082,
         "url": "https://callback.senler.ru/webhook/vk/1078808",
         "secret_key": "…", "status": "ok"}
    ]}}

Вывод: факт подключения проверяется штатным методом VK по токену сообщества —
ключ API Senler не нужен вовсе (ни для этой проверки, ни для чего-либо ещё в
проекте). Senler определяется по домену `senler.ru` в адресе callback-сервера,
а НЕ по названию: название редактирует владелец сообщества в интерфейсе VK, оно
ничего не гарантирует. Сервер со статусом не `ok` — сломанная воронка, заявки
через неё не попадут, поэтому подключённым это не считаем.

Чистые функции, без сети — сетевой поход за списком серверов делает
`integrations/vk_community.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

_SENLER_DOMAIN = "senler.ru"
_STATUS_OK = "ok"
_NOT_FOUND_REASON = "в сообществе нет callback-сервера Senler"


@dataclass(frozen=True, slots=True)
class SenlerCheck:
    """Итог проверки: подключён ли Senler, id его проекта, причина отказа."""

    connected: bool
    project_id: str | None
    reason: str


def _is_senler_url(url: str) -> bool:
    """Домен `senler.ru` (или его поддомен, напр. `callback.senler.ru`)."""
    host = (urlsplit(url).hostname or "").lower()
    return host == _SENLER_DOMAIN or host.endswith(f".{_SENLER_DOMAIN}")


def _project_id(url: str) -> str | None:
    """Последний сегмент пути адреса — id проекта Senler."""
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    return segments[-1] if segments else None


def detect_senler(servers: Sequence[Mapping[str, Any]]) -> SenlerCheck:
    """Разобрать ответ `groups.getCallbackServers` и вынести вердикт по Senler.

    Ищем ПЕРВЫЙ сервер на домене `senler.ru`; остальные (не-Senler) серверы в
    том же ответе игнорируются. Найденный, но не в статусе `ok`, — явный отказ
    с причиной, а не молчаливое «не подключён».
    """
    for server in servers:
        url = str(server.get("url", ""))
        if not _is_senler_url(url):
            continue
        status = str(server.get("status", ""))
        project_id = _project_id(url)
        if status != _STATUS_OK:
            return SenlerCheck(
                connected=False,
                project_id=project_id,
                reason=f"callback-сервер Senler в статусе {status!r}, а не 'ok'",
            )
        return SenlerCheck(connected=True, project_id=project_id, reason="")
    return SenlerCheck(connected=False, project_id=None, reason=_NOT_FOUND_REASON)


__all__ = ["SenlerCheck", "detect_senler"]
