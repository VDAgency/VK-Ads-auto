"""KotbotAdapter — автоматизация интерфейса kotbot через Playwright. СКЕЛЕТ.

У kotbot нет API, поэтому канал реализуется браузерной автоматизацией (хрупкий путь,
PROJECT.md §8) — нужны доступы к кабинету kotbot и Playwright. До их получения методы
бросают `NotImplementedError`, а health_check возвращает False (канал не сконфигурирован).
Playwright намеренно НЕ импортируется, пока канал не введён в строй.
"""

from __future__ import annotations

from integrations.adapter import PlatformAdapter

_PENDING = "kotbot: нужны доступы к кабинету + Playwright-сценарии — pending"


class KotbotAdapter(PlatformAdapter):
    """Адаптер kotbot (Playwright). Скелет до получения доступов к kotbot."""

    async def health_check(self) -> bool:
        return False

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        raise NotImplementedError(_PENDING)

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        raise NotImplementedError(_PENDING)

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        raise NotImplementedError(_PENDING)

    async def launch(self, campaign_id: str) -> None:
        raise NotImplementedError(_PENDING)

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        raise NotImplementedError(_PENDING)
