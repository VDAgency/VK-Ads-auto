"""Нативное меню команд Telegram (синяя кнопка «Меню» у поля ввода).

Список из 5 команд регистрируется один раз при старте бота (`bot/main.py`) и
дублируется на `/start`. Тексты reply-кнопок роутятся в сценарии не здесь, а в
самих хендлерах (кнопка матчится вместе со своей командой через `or_f`), чтобы
не заводить центральный диспетчер и не дублировать логику.
"""

from __future__ import annotations

from aiogram import Bot
from aiogram.types import BotCommand

# (команда, описание в синем меню). Порядок = порядок показа.
_COMMANDS: tuple[tuple[str, str], ...] = (
    ("send_brief", "Отправить клиенту бриф"),
    ("pending", "Кто ещё не прислал бриф"),
    ("stats", "Статистика рекламных кабинетов"),
    ("link_userbot", "Подключить юзер-бота"),
    ("help", "Как работает бот"),
)


def bot_commands() -> list[BotCommand]:
    """Список команд для `setMyCommands`."""
    return [BotCommand(command=name, description=desc) for name, desc in _COMMANDS]


async def setup_bot_commands(bot: Bot) -> None:
    """Зарегистрировать нативное меню команд (идемпотентно)."""
    await bot.set_my_commands(bot_commands())
