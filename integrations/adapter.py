"""Контракт исходящего интеграционного слоя.

Ядро никогда не обращается к рекламным площадкам напрямую — только через
`PlatformAdapter`. Конкретные реализации (`VkApiAdapter`, `KotbotAdapter`)
добавляются в следующих фазах; здесь — только интерфейс (шов расширения).
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class PlatformAdapter(ABC):
    """Абстрактный адаптер рекламной площадки.

    Все методы асинхронны (сетевой I/O). Реализация выбирается через
    per-account `IntegrationConfig` (канал по умолчанию + ручной переключатель)
    с health-check и фолбэком.
    """

    @abstractmethod
    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        """Создать рекламный кабинет; вернуть его внешний идентификатор."""
        raise NotImplementedError

    @abstractmethod
    async def create_campaign(self, cabinet_id: str, goal: str) -> str:
        """Создать кампанию под выбранную цель; вернуть её идентификатор."""
        raise NotImplementedError

    @abstractmethod
    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        """Загрузить креатив в кампанию; вернуть идентификатор креатива."""
        raise NotImplementedError

    @abstractmethod
    async def launch(self, campaign_id: str) -> None:
        """Запустить кампанию (перевести в активное состояние)."""
        raise NotImplementedError

    @abstractmethod
    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        """Вернуть срез метрик кампании (показы, расход, результат, CPL/CPC, CTR)."""
        raise NotImplementedError
