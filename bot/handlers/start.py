"""Стартовый хендлер и помощь."""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.access import OperatorOnly

router = Router(name="start")
router.message.filter(OperatorOnly())


@router.message(CommandStart())
async def start(message: Message) -> None:
    """Поприветствовать оператора и показать основную команду."""
    await message.answer(
        "Бот VK-Ads-auto.\n\nКоманда /send_brief — отправить клиенту ссылку на бриф."
    )
