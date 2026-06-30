"""Точка входа Telegram-бота (aiogram 3, long-polling).

Бот — тонкий клиент: команды только от операторов, вся логика — в сервисах ядра.
Запуск: `python -m bot.main` (сервис `bot` в docker-compose).
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from config.settings import get_settings

from bot.handlers import send_brief, start


def build_dispatcher() -> Dispatcher:
    """Собрать диспетчер со всеми роутерами."""
    dispatcher = Dispatcher()
    dispatcher.include_router(start.router)
    dispatcher.include_router(send_brief.router)
    return dispatcher


async def run() -> None:
    """Запустить long-polling. Требует заданного `BOT_TOKEN`."""
    token = get_settings().bot_token.get_secret_value()
    if not token:
        raise RuntimeError("BOT_TOKEN не задан — бот не запускается")
    bot = Bot(token=token)
    dispatcher = build_dispatcher()
    await dispatcher.start_polling(bot)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
