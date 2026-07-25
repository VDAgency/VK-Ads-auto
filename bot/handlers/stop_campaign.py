"""Команда `/stop_campaign <id>`: остановить кампанию (spec 2026-07-17 §5).

Тонкий хендлер: разбираем номер кампании из аргумента и зовём ядро
(`POST /api/v1/campaigns/{id}/stop`) — вся логика (скоуп тенанта, выбор канала,
вызов площадки, смена статуса) живёт в `services/launch_service`.
Ответы честные: если площадку останавливать было нечего или канал не принял
остановку — говорим прямо, успех не имитируем (CLAUDE.md §7).
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import CampaignNotFound, CampaignStopFailed, CoreUnavailable

router = Router(name="stop_campaign")
router.message.filter(OperatorOnly())

_USAGE = (
    "Укажите номер кампании: /stop_campaign 12\nНомер виден в карточке брифа после запуска рекламы."
)
_NOT_FOUND = "Кампания не найдена. Проверьте номер: /stop_campaign 12"
_STOP_FAILED = (
    "⚠️ Канал не принял остановку — кампания могла остаться запущенной.\n"
    "Проверьте канал (VK API / kotbot) и повторите команду."
)
_UNAVAILABLE = "Сервис временно недоступен, попробуйте позже."


def _parse_campaign_id(args: str | None) -> int | None:
    """Номер кампании из аргумента команды; `None` — аргумента нет или он не число."""
    raw = (args or "").strip()
    return int(raw) if raw.isdigit() else None


@router.message(Command("stop_campaign"))
async def stop_campaign(message: Message, command: CommandObject) -> None:
    """Остановить кампанию по номеру и отчитаться оператору."""
    campaign_id = _parse_campaign_id(command.args)
    if campaign_id is None:
        await message.answer(_USAGE)
        return
    try:
        result = await api_client.stop_campaign(campaign_id)
    except CampaignNotFound:
        await message.answer(_NOT_FOUND)
        return
    except CampaignStopFailed:
        await message.answer(_STOP_FAILED)
        return
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        return
    if result.external_id is None:
        await message.answer(
            f"⏹ Кампания №{result.campaign_id} помечена как остановленная.\n"
            "На площадке останавливать было нечего: у кампании нет внешнего id "
            "(она не создавалась в VK/kotbot)."
        )
        return
    await message.answer(
        f"⏹ Кампания №{result.campaign_id} остановлена на площадке (id {result.external_id}). "
        "Показы прекращены, деньги не тратятся."
    )
