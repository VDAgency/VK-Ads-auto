"""Точка входа Telegram-бота (aiogram 3, long-polling).

Бот — тонкий клиент: команды только от операторов, вся логика — в сервисах ядра.
Запуск: `python -m bot.main` (сервис `bot` в docker-compose).
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from config.settings import get_settings

from bot import api_client, kotbot_watch, userbot_watch
from bot.handlers import (
    admin,
    brief_card,
    creative,
    link_kotbot,
    link_userbot,
    pending,
    send_brief,
    start,
    stats,
    stranger,
)
from bot.handlers import help as help_handler
from bot.menu import setup_bot_commands


def build_dispatcher() -> Dispatcher:
    """Собрать диспетчер со всеми роутерами."""
    dispatcher = Dispatcher()
    dispatcher.include_router(start.router)
    dispatcher.include_router(send_brief.router)
    dispatcher.include_router(pending.router)
    dispatcher.include_router(brief_card.router)
    dispatcher.include_router(creative.router)
    dispatcher.include_router(stats.router)
    dispatcher.include_router(admin.router)
    dispatcher.include_router(link_userbot.router)
    dispatcher.include_router(link_kotbot.router)
    dispatcher.include_router(help_handler.router)
    # Визитка для чужих — последней: ловит только не-операторские апдейты.
    dispatcher.include_router(stranger.router)
    return dispatcher


async def run() -> None:
    """Запустить long-polling. Требует заданного `BOT_TOKEN`."""
    token = get_settings().bot_token.get_secret_value()
    if not token:
        raise RuntimeError("BOT_TOKEN не задан — бот не запускается")
    bot = Bot(token=token)
    await setup_bot_commands(bot)
    dispatcher = build_dispatcher()
    # Фоновые health-check-поллеры (без BASE_URL соответствующего сервиса — не
    # запускаем): userbot — баннер в /send_brief при неавторизованной сессии;
    # kotbot — уведомление операторам на переходе healthy→unhealthy.
    pollers: list[asyncio.Task[None]] = []
    if api_client.userbot_configured():
        pollers.append(asyncio.create_task(userbot_watch.poll_forever()))
    if api_client.kotbot_configured():
        pollers.append(asyncio.create_task(kotbot_watch.poll_forever(bot)))
    try:
        await dispatcher.start_polling(bot)
    finally:
        for poller in pollers:
            poller.cancel()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
