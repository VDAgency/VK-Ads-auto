"""Кэш точки подключения, на которой оператор в последний раз успешно вышел на связь.

Строка сессии сама хранит `dc_id/ip/port`, но не хранит транспорт и не годится для
показа на экране диагностики. Поэтому держим отдельный маленький файл рядом с сессиями.

Внутри только публичные адреса дата-центров — шифровать нечего, и читаемость глазами
здесь важнее: при разборе инцидента на проде это первое, куда смотрят.

Кэш никогда не должен мешать старту: битый или недоступный файл — это предупреждение
в лог и пустой кэш, а не исключение.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

from userbot.endpoints import Endpoint, Transport

logger = logging.getLogger(__name__)

_FILENAME = "endpoints.json"


class EndpointCache:
    """Последняя удачная точка подключения по каждому оператору."""

    def __init__(self, sessions_dir: str) -> None:
        self._path = Path(sessions_dir) / _FILENAME
        self._data: dict[str, Endpoint] = {}
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            raw: Any = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("кэш точек подключения нечитаем — начинаю с пустого")
            return
        if not isinstance(raw, dict):
            logger.warning("кэш точек подключения имеет неожиданный формат — игнорирую")
            return
        for key, value in raw.items():
            endpoint = _parse(value)
            if endpoint is not None:
                self._data[str(key)] = endpoint

    def get(self, sender_id: int) -> Endpoint | None:
        """Точка оператора; `None` — ещё не сохраняли."""
        return self._data.get(str(sender_id))

    def remember(self, sender_id: int, endpoint: Endpoint) -> None:
        """Запомнить удачную точку. Сбой записи не должен ронять отправку."""
        if self._data.get(str(sender_id)) == endpoint:
            return
        self._data[str(sender_id)] = endpoint
        try:
            self._flush()
        except OSError:
            logger.warning("не удалось записать кэш точек подключения — продолжаю")

    def _flush(self) -> None:
        """Атомарная запись, как в SessionStore: tmp → права → replace."""
        payload = {
            key: {
                "dc_id": endpoint.dc_id,
                "ip": endpoint.ip,
                "port": endpoint.port,
                "transport": endpoint.transport.value,
            }
            for key, endpoint in sorted(self._data.items())
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(tmp, self._path)


def _parse(value: Any) -> Endpoint | None:
    """Одна запись кэша → `Endpoint`; мусор → `None` (запись просто пропускается)."""
    if not isinstance(value, dict):
        return None
    try:
        dc_id = int(value["dc_id"])
        ip = str(value["ip"])
        port = int(value["port"])
    except (KeyError, TypeError, ValueError):
        return None
    if not ip:
        return None
    try:
        transport = Transport(str(value.get("transport", Transport.FULL.value)))
    except ValueError:
        transport = Transport.FULL
    return Endpoint(dc_id=dc_id, ip=ip, port=port, transport=transport)
