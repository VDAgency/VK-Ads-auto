"""Экран диагностики юзербота (`/userbot_status`) и его кнопки.

Тонкий хендлер: состояние читается из сервиса (дешёвый запрос к памяти), вёрстка —
в `services.userbot_report`. Кнопки делают то, что стоит денег по времени:
«Проверить сейчас» ходит в Telegram, «Точки подключения» снимает матрицу
достижимости — то, что раньше приходилось делать вручную по SSH.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InaccessibleMessage, Message
from config.settings import get_settings
from services.userbot_report import SessionReport, render_endpoints, render_status

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import UserbotUnavailable
from bot.keyboards import userbot_status_keyboard

router = Router(name="userbot_status")
router.message.filter(OperatorOnly())
router.callback_query.filter(OperatorOnly())

_NOT_CONFIGURED = (
    "🤖 <b>Юзербот</b>\n\nСервис не настроен на этом сервере (пустой USERBOT_BASE_URL)."
)
_UNAVAILABLE = "Сервис юзербота недоступен, попробуйте позже."
_CHECKING = "Проверяю сессию — это занимает до минуты…"
_PROBING = "Снимаю матрицу точек подключения…"


async def _render_screen() -> str:
    """Текст экрана состояния; `UserbotUnavailable` — отдельная честная ветка."""
    settings = get_settings()
    try:
        sessions = await api_client.userbot_health_all()
    except UserbotUnavailable:
        return render_status(service_up=False, proxy_configured=False, sessions=[])
    reports = [
        SessionReport(
            sender_id=sender_id,
            authorized=state.authorized,
            unreachable=state.unreachable,
            phone=state.phone,
            is_operator=sender_id in settings.operator_telegram_ids,
        )
        for sender_id, state in sorted(sessions.items())
    ]
    return render_status(service_up=True, proxy_configured=False, sessions=reports)


@router.message(Command("userbot_status"))
async def show_status(message: Message) -> None:
    """`/userbot_status` — состояние сервиса и сессий операторов."""
    if not api_client.userbot_configured():
        await message.answer(_NOT_CONFIGURED, parse_mode="HTML")
        return
    await message.answer(
        await _render_screen(), parse_mode="HTML", reply_markup=userbot_status_keyboard()
    )


def _target(callback: CallbackQuery) -> Message | None:
    message = callback.message
    if message is None or isinstance(message, InaccessibleMessage):
        return None
    return message


@router.callback_query(F.data == "ubstatus:probe")
async def probe_now(callback: CallbackQuery) -> None:
    """«Проверить сейчас» — форсировать проверку сессии вызвавшего оператора."""
    message = _target(callback)
    if message is None:
        await callback.answer()
        return
    await callback.answer(_CHECKING)
    try:
        await api_client.userbot_probe(callback.from_user.id)
    except UserbotUnavailable:
        await message.answer(_UNAVAILABLE)
        return
    await message.answer(
        await _render_screen(), parse_mode="HTML", reply_markup=userbot_status_keyboard()
    )


@router.callback_query(F.data == "ubstatus:endpoints")
async def show_endpoints(callback: CallbackQuery) -> None:
    """«Точки подключения» — матрица достижимости изнутри контейнера."""
    message = _target(callback)
    if message is None:
        await callback.answer()
        return
    await callback.answer(_PROBING)
    try:
        rows = await api_client.userbot_endpoints()
    except UserbotUnavailable:
        await message.answer(_UNAVAILABLE)
        return
    await message.answer(render_endpoints(rows), parse_mode="HTML")
