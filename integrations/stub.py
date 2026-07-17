"""Заглушка адаптера площадки: подготовка кампании без боевых мутаций VK.

Боевое создание кабинетов/кампаний VK разрешено только после подтверждения
агентского статуса ИП (CLAUDE.md §1.4). Пока статус не подтверждён или нет токена,
запуск идёт через эту заглушку: методы возвращают синтетические id и ничего не
мутируют во внешних системах. Точка переключения на боевой `VkApiAdapter` —
`services/launch_service.py::_select_adapter`.
"""

from __future__ import annotations

from integrations.adapter import PlatformAdapter


class StubAdapter(PlatformAdapter):
    """Ничего не мутирует; отдаёт синтетические идентификаторы (запуск-заглушка)."""

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        return f"stub-cabinet-{account_id}-{client_ref}"

    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        return f"stub-campaign-{cabinet_id}"

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        return f"stub-creative-{campaign_id}"

    async def launch(self, campaign_id: str) -> None:
        return None

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        return {}

    async def health_check(self) -> bool:
        return True
