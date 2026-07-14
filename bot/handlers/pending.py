"""Трекинг брифов: кто ещё не прислал и кто прислал недавно (команда №3).

PR-A: точка входа (`/pending` + кнопка «📥 Ждём бриф») и заглушка ответа.
PR-B нарастит запрос к ядру (`GET /api/v1/invites`) и рендер двух секций.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.types import Message

from bot.access import OperatorOnly
from bot.keyboards import BTN_PENDING

router = Router(name="pending")
router.message.filter(OperatorOnly())


@router.message(or_f(Command("pending"), F.text == BTN_PENDING))
async def show_pending(message: Message) -> None:
    """Показать, кого ждём и кто прислал недавно (пока — заглушка)."""
    await message.answer(
        "📥 Трекинг брифов скоро будет здесь: кто получил анкету, но ещё не прислал, "
        "и кто прислал за последнюю неделю."
    )
