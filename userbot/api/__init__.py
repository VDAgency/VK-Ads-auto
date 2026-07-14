"""HTTP-роутеры userbot-сервиса. Клиент берётся из `app.state.client`."""

from __future__ import annotations

from fastapi import Request

from userbot.telethon_client import UserbotClient


def get_client(request: Request) -> UserbotClient:
    """Dependency: вернуть `UserbotClient`, собранный в lifespan (см. main.py)."""
    client: UserbotClient = request.app.state.client
    return client
