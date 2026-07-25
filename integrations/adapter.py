"""Контракт исходящего интеграционного слоя.

Ядро никогда не обращается к рекламным площадкам напрямую — только через
`PlatformAdapter`. Конкретные реализации (`VkApiAdapter`, `KotbotAdapter`)
добавляются в следующих фазах; здесь — только интерфейс (шов расширения).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from services.mapping import CampaignSpec


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

    @abstractmethod
    async def health_check(self) -> bool:
        """Проверить доступность канала. Используется роутером для выбора и фолбэка."""
        raise NotImplementedError

    # Ниже — необязательные операции: базовая реализация нужна, чтобы заглушка и
    # скелет kotbot оставались рабочими, а боевой адаптер их переопределял.

    async def get_status(self, campaign_id: str) -> str:
        """Статус кампании на площадке. По умолчанию площадка его не сообщает."""
        return "unknown"

    async def stop(self, campaign_id: str) -> None:
        """Остановить кампанию. По умолчанию не поддерживается каналом."""
        raise NotImplementedError("stop is not supported by this adapter")

    async def create_campaign_from_spec(
        self,
        cabinet_id: str,
        spec: CampaignSpec,
        *,
        creative_ref: str | None = None,
        title: str | None = None,
        body: str | None = None,
        budget_limit_day: float | None = None,
    ) -> str:
        """Создать кампанию целиком по спеке; вернуть её идентификатор.

        Единственная операция запуска, которую знает ядро: как площадка разложит
        спеку по своим уровням (у VK — ad_plan → ad_group → banner) — её дело
        (CLAUDE.md §1.3). Дефолт повторяет прежнее поведение: одна кампания по
        цели из спеки плюс, при наличии, загрузка креатива.
        """
        campaign_id = await self.create_campaign(cabinet_id, spec.objective)
        if creative_ref:
            await self.upload_creative(campaign_id, creative_ref)
        return campaign_id
