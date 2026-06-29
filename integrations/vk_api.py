"""VkApiAdapter — прямой VK Ads API. СКЕЛЕТ (живые вызовы ещё не реализованы).

Точные эндпоинты и поля VK Ads API сверяются с живой документацией (context7 / офсайт
VK) и НЕ выдумываются (CLAUDE.md §6). Боевое создание кабинетов требует подтверждения
агентского статуса ИП. До выполнения этих условий мутирующие методы бросают
`NotImplementedError`. Токен берётся из per-account конфигурации (см. config.settings).
"""

from __future__ import annotations

from pydantic import SecretStr

from integrations.adapter import PlatformAdapter

_PENDING = "VK Ads API: эндпоинты/поля сверяются с живой докой (context7) — pending"


class VkApiAdapter(PlatformAdapter):
    """Адаптер прямого VK Ads API. Хранит токен; вызовы — после сверки с докой."""

    def __init__(self, access_token: SecretStr) -> None:
        self._access_token = access_token

    async def health_check(self) -> bool:
        # TODO: реальный health = read-only запрос к VK (scope read_user_info).
        # Пока канал не введён в строй — считаем недоступным.
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
