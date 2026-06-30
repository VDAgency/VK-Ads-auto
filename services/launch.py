"""Оркестрация запуска кампании через `PlatformAdapter`.

Ядро не знает про площадку — работает с любым адаптером. Последовательность:
создать кампанию по objective из спеки → (опц.) загрузить креатив → запустить.
Реальные мутации идут только при явном вызове с боевым адаптером и подтверждении.
"""

from __future__ import annotations

from dataclasses import dataclass

from integrations.adapter import PlatformAdapter

from services.mapping import CampaignSpec


@dataclass(frozen=True)
class LaunchResult:
    """Результат запуска: id кампании и факт запуска."""

    campaign_id: str
    launched: bool


async def run_campaign(
    adapter: PlatformAdapter,
    cabinet_id: str,
    spec: CampaignSpec,
    creative_ref: str | None = None,
) -> LaunchResult:
    """Создать кампанию по спеке, при наличии — загрузить креатив, запустить."""
    campaign_id = await adapter.create_campaign(cabinet_id, spec.objective)
    if creative_ref:
        await adapter.upload_creative(campaign_id, creative_ref)
    await adapter.launch(campaign_id)
    return LaunchResult(campaign_id=campaign_id, launched=True)


def launch_confirmation(result: LaunchResult) -> str:
    """Сообщение оператору после запуска (Сценарий A, шаг 9)."""
    return f"Кабинет создан, кампания запущена (id {result.campaign_id}), добавлено в отслеживание."
