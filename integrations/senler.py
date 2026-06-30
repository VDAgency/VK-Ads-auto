"""Senler — интеграция для цели «заявка через Senler». СКЕЛЕТ (отложено).

На MVP запускаем только подписки; цель «заявка через Senler» (через «написать
сообщение», бот Senler ловит обращение) — будущая доработка. До неё методы
бросают `NotImplementedError`. Нужен API-ключ Senler и подключённое сообщество.
"""

from __future__ import annotations

_PENDING = "Senler: цель «заявка» отложена (нужен API-ключ Senler) — pending"


async def link_lead_capture(community_id: str) -> None:
    """Подключить захват заявок Senler к сообществу. Отложено."""
    raise NotImplementedError(_PENDING)
