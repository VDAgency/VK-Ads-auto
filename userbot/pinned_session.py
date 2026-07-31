"""Сессия Telethon, у которой адрес дата-центра проходит через наш реестр точек.

Это ключевое место всей отказоустойчивости. Telethon задаёт адрес дата-центра ровно
через `Session.set_dc`, и зовёт его в двух местах:

1. `connect()` — если у сессии пустой адрес, ставит жёсткую константу
   `DEFAULT_DC_ID=2 / 149.154.167.51:443`. Именно поэтому новая авторизация с нашего
   сервера не проходила: этот адрес закрыт.
2. `_switch_dc()` — после `PhoneMigrateError`, когда Telegram сообщает домашний
   дата-центр номера. Адрес берётся из `help.getConfig()`, а там всегда порт 443,
   то есть миграция уводила на заблокированный `149.154.167.91:443`.

Один переопределённый метод закрывает оба сценария. Мы не трогаем приватные методы
Telethon, поэтому обновление библиотеки решение не ломает.

`dc_id` передаётся родителю КАК ЕСТЬ — подменяются только адрес и порт. Смена `dc_id`
обнуляет ключ авторизации и разлогинивает оператора.
"""

from __future__ import annotations

import logging

from telethon.sessions import StringSession

from userbot.endpoints import Endpoint, EndpointResolver

logger = logging.getLogger(__name__)


class PinnedStringSession(StringSession):  # type: ignore[misc] # у Telethon нет стабов
    """`StringSession`, подменяющая адрес дата-центра на известный рабочий."""

    def __init__(self, string: str | None, resolver: EndpointResolver) -> None:
        super().__init__(string)
        self._resolver = resolver
        # Последняя применённая точка — для логов и диагностики.
        self.applied: Endpoint | None = None

    def set_dc(self, dc_id: int, server_address: str, port: int) -> None:
        """Подменить адрес дата-центра на точку из реестра (тот же `dc_id`)."""
        endpoint = self._resolver.preferred(dc_id, fallback_ip=server_address, fallback_port=port)
        self.applied = endpoint
        if (endpoint.ip, endpoint.port) != (server_address, port):
            logger.info("set_dc(%s, %s:%s) → %s", dc_id, server_address, port, endpoint.label())
        super().set_dc(dc_id, endpoint.ip, endpoint.port)


def session_endpoint(session_str: str | None, resolver: EndpointResolver) -> Endpoint | None:
    """Точка, зашитая в сохранённой строке сессии; `None` — строка пуста или битая.

    Строка сессии уже несёт `dc_id/ip/port` последнего удачного подключения, так что
    это самый дешёвый кандидат для перебора — сеть не трогаем.
    """
    if not session_str:
        return None
    try:
        parsed = StringSession(session_str)
    except (ValueError, TypeError):
        logger.warning("не удалось разобрать строку сессии — пропускаю её точку")
        return None
    if not parsed.server_address or not parsed.dc_id:
        return None
    return Endpoint(
        dc_id=parsed.dc_id,
        ip=parsed.server_address,
        port=parsed.port,
        transport=resolver.default_transport,
    )
