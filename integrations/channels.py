"""Выбор канала интеграции (VK API / kotbot) с health-check и фолбэком.

Ядро не знает про конкретные площадки — оно просит у роутера рабочий
`PlatformAdapter`. Роутер выбирает канал по конфигу (дефолт + ручной
переключатель), проверяет здоровье и при необходимости падает на запасной канал
с понятной ошибкой для обратной связи оператору (PROJECT.md §5).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum

from integrations.adapter import PlatformAdapter


class Channel(Enum):
    """Канал исходящей интеграции. Значения совпадают с `IntegrationConfig.default_channel`."""

    VK_API = "vk_api"
    KOTBOT = "kotbot"


class NoHealthyChannelError(RuntimeError):
    """Ни один из каналов не прошёл health-check."""

    def __init__(self, tried: list[Channel]) -> None:
        self.tried = tried
        names = ", ".join(c.value for c in tried)
        super().__init__(f"No healthy integration channel (tried: {names})")


@dataclass(frozen=True)
class ChannelConfig:
    """Конфиг выбора канала (per-account). `forced` — ручной переключатель."""

    default: Channel
    forced: Channel | None = None


class ChannelRouter:
    """Маршрутизатор: отдаёт здоровый адаптер по конфигу с фолбэком на запасной."""

    def __init__(
        self,
        adapters: Mapping[Channel, PlatformAdapter],
        config: ChannelConfig,
    ) -> None:
        self._adapters = dict(adapters)
        self._config = config

    def _priority(self) -> list[Channel]:
        """Порядок попыток: сначала forced|default, затем остальные каналы."""
        primary = self._config.forced or self._config.default
        rest = [c for c in self._adapters if c != primary]
        return [primary, *rest]

    async def select(self) -> tuple[Channel, PlatformAdapter]:
        """Вернуть (канал, адаптер) первого здорового канала или бросить ошибку."""
        tried: list[Channel] = []
        for channel in self._priority():
            adapter = self._adapters.get(channel)
            if adapter is None:
                continue
            tried.append(channel)
            if await adapter.health_check():
                return channel, adapter
        raise NoHealthyChannelError(tried)
