"""HTTP-роутеры kotbot-сервиса. Автоматизация берётся из `app.state.automation`."""

from __future__ import annotations

from fastapi import Request

from kotbot.service import KotbotAutomation


def get_automation(request: Request) -> KotbotAutomation:
    """Dependency: вернуть `KotbotAutomation`, собранную в lifespan (см. main.py)."""
    automation: KotbotAutomation = request.app.state.automation
    return automation
