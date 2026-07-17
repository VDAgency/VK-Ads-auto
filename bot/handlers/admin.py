"""Команда `/admin`: выдать оператору ссылку входа в веб-админку.

Ссылку генерирует сам бот (подписанный токен, `services/admin_auth`) и шлёт оператору —
публичного эндпоинта «выдать ссылку» нет (иначе любой, зная Telegram-ID оператора,
выпустил бы себе доступ). Двойной гейт: `OperatorOnly` в боте + подпись секретом ядра.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from config.settings import get_settings
from services.admin_auth import generate_admin_link

from bot.access import OperatorOnly

router = Router(name="admin")
router.message.filter(OperatorOnly())


@router.message(Command("admin"))
async def admin_link(message: Message) -> None:
    """Прислать оператору одноразовую ссылку входа в веб-админку (15 минут)."""
    user = message.from_user
    if user is None:
        return
    settings = get_settings()
    token = generate_admin_link(user.id, settings.secret_key.get_secret_value())
    url = f"{settings.public_base_url}/admin.html?token={token}"
    await message.answer(
        "🔐 Вход в веб-админку. Ссылка действует 15 минут, не пересылайте её:\n" + url
    )
