"""Точка входа Telegram-бота (aiogram 3, long-polling).

Бот — тонкий клиент: команды только от операторов, вся логика — в сервисах ядра.
Запуск: `python -m bot.main` (сервис `bot` в docker-compose).
"""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher, Router
from config.settings import get_settings

from bot import api_client, kotbot_watch, userbot_watch
from bot.handlers import (
    ad_accounts,
    admin,
    brief_card,
    creative,
    link_kotbot,
    link_userbot,
    pending,
    send_brief,
    start,
    stats,
    stop_campaign,
    stranger,
    surfaces,
    userbot_status,
)
from bot.handlers import help as help_handler
from bot.menu import setup_bot_commands


def routers() -> list[Router]:
    """Роутеры в порядке разбора апдейта. Порядок значим — см. комментарии.

    Отдельно от `build_dispatcher`, потому что роутеры — синглтоны модулей:
    привязать их к диспетчеру можно один раз за процесс, а проверять порядок
    в тестах нужно без побочных эффектов.
    """
    return [
        start.router,
        # Справка — раньше сценариев с FSM: их catch-all хендлеры съели бы `/help`,
        # набранный посреди ввода токена или креатива, то есть ровно тогда, когда
        # справка и нужна. Перехватить чужое help-роутер не может: один
        # message-хендлер с узким `Command("help")` и два callback-хендлера `help:`.
        help_handler.router,
        send_brief.router,
        pending.router,
        brief_card.router,
        creative.router,
        stats.router,
        stop_campaign.router,
        admin.router,
        link_userbot.router,
        link_kotbot.router,
        ad_accounts.router,
        surfaces.router,
        userbot_status.router,
        # Визитка для чужих — последней: ловит только не-операторские апдейты.
        stranger.router,
    ]


def build_dispatcher() -> Dispatcher:
    """Собрать диспетчер со всеми роутерами."""
    dispatcher = Dispatcher()
    for router in routers():
        dispatcher.include_router(router)
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
        pollers.append(asyncio.create_task(userbot_watch.poll_forever(bot)))
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
