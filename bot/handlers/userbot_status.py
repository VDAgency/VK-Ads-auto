"""Экран диагностики юзербота (`/userbot_status`).

Тонкий хендлер: состояние берём свежим запросом к сервису (оператор жмёт команду
именно тогда, когда хочет знать «прямо сейчас», а не что закешировал поллер),
вёрстка — в `services.userbot_report`.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from config.settings import get_settings
from services.userbot_report import SessionReport, render_status

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import UserbotUnavailable

router = Router(name="userbot_status")
router.message.filter(OperatorOnly())

_NOT_CONFIGURED = (
    "🤖 <b>Юзербот</b>\n\nСервис не настроен на этом сервере (пустой USERBOT_BASE_URL)."
)


@router.message(Command("userbot_status"))
async def show_status(message: Message) -> None:
    """`/userbot_status` — состояние сервиса и сессий операторов."""
    if not api_client.userbot_configured():
        await message.answer(_NOT_CONFIGURED, parse_mode="HTML")
        return

    settings = get_settings()
    try:
        sessions = await api_client.userbot_health_all()
    except UserbotUnavailable:
        await message.answer(
            render_status(service_up=False, proxy_configured=False, sessions=[]),
            parse_mode="HTML",
        )
        return

    reports = [
        SessionReport(
            sender_id=sender_id,
            authorized=state.authorized,
            unreachable=state.error == "unreachable",
            phone=state.phone,
            is_operator=sender_id in settings.operator_telegram_ids,
        )
        for sender_id in sorted(sessions)
        for state in (sessions[sender_id],)
    ]
    await message.answer(
        render_status(
            service_up=True,
            # Прокси настраивается в самом сервисе userbot; бот знает только факт.
            proxy_configured=False,
            sessions=reports,
        ),
        parse_mode="HTML",
    )
