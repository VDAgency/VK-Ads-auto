"""Стартовый хендлер: приветствие, постоянная клавиатура, нативное меню."""

from __future__ import annotations

from aiogram import Bot, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.access import OperatorOnly
from bot.keyboards import main_menu_keyboard
from bot.menu import setup_bot_commands

router = Router(name="start")
router.message.filter(OperatorOnly())

_WELCOME = (
    "👋 Это бот <b>VK-Ads-auto</b> — запуск и ведение рекламы во ВКонтакте под ключ.\n\n"
    "<b>Что я умею:</b>\n"
    "📨 Отправить клиенту бриф и проследить, что он его заполнил\n"
    "🚀 Завести и запустить рекламный кабинет\n"
    "📊 Показать статистику по запущенным кабинетам\n\n"
    "<b>С чего начать:</b>\n"
    "• Кнопка «📨 Отправить бриф» — отправить клиенту анкету\n"
    "• Кнопка «📥 Ждём бриф» — кто ещё не прислал\n"
    "• Синее меню слева от поля ввода — все команды\n\n"
    "Нужна помощь — /help"
)


@router.message(CommandStart())
async def start(message: Message, bot: Bot) -> None:
    """Поприветствовать оператора, показать клавиатуру и обновить меню команд."""
    await setup_bot_commands(bot)
    await message.answer(_WELCOME, parse_mode="HTML", reply_markup=main_menu_keyboard())
