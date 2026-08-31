"""Команда `/set_password`: задать пароль входа в веб-кабинет оператора.

Экран входа в веб-админку (`web/components/admin/LoginScreen.tsx`) прямо говорит
оператору: «Пароль задаётся командой /set_password в боте» — самой команды не
было, хотя эндпоинты (`POST /admin/login`, `POST /admin/password`,
spec 2026-08-31, `core/api/v1/admin.py`) уже на проде. Без неё задать пароль
было нечем — вход по одноразовой ссылке `/admin` при этом продолжал работать.

Тонкий хендлер: ввод и рендер здесь, всё остальное — в ядре через
`bot/api_client` (CLAUDE.md §1.3). Сообщение с паролем удаляется из чата сразу
после приёма — тот же приём, что в `/senler_token` и `/cabinets`
(`bot/handlers/senler.py`, `bot/handlers/ad_accounts.py`).
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from config.settings import get_settings

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import CoreUnavailable, WeakPassword
from bot.states import SetPassword

logger = logging.getLogger(__name__)

router = Router(name="set_password")
router.message.filter(OperatorOnly())

_ASK_PASSWORD = (
    "Пришлите пароль для входа в веб-кабинет сообщением — я сразу удалю его из "
    "чата.\n\nНе меньше 10 символов."
)
_UNAVAILABLE = "Сервис временно недоступен, попробуйте позже."
_EMPTY_PASSWORD = "Пустое сообщение — пришлите пароль ещё раз."


def _redact(value: str) -> str:
    """Замаскировать секрет для логов: длину видно, содержимое — нет."""
    return f"<redacted:{len(value)}>"


async def _delete_secret(message: Message) -> None:
    """Стереть сообщение с паролем из чата. Отказ удаления гасим — не блокер."""
    try:
        await message.delete()
    except Exception:  # noqa: BLE001 — Telegram может запретить удаление
        logger.debug("set_password: message.delete() failed, continuing")


@router.message(Command("set_password"))
async def start(message: Message, state: FSMContext) -> None:
    """`/set_password` — задать (или сменить) пароль входа в веб-кабинет."""
    await state.set_state(SetPassword.entering_password)
    await message.answer(_ASK_PASSWORD)


@router.message(StateFilter(SetPassword.entering_password))
async def got_password(message: Message, state: FSMContext) -> None:
    """Принять пароль, немедленно стереть сообщение и задать его через ядро."""
    password = (message.text or "").strip()
    await _delete_secret(message)
    if not password:
        await message.answer(_EMPTY_PASSWORD)
        return
    await state.clear()

    user = message.from_user
    if user is None:
        return
    logger.info("operator password received: %s", _redact(password))

    try:
        await api_client.set_operator_password(user.id, password)
    except WeakPassword as exc:
        await message.answer(f"❌ {exc.reason}")
        return
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        return

    url = f"{get_settings().public_base_url}/admin.html"
    await message.answer(
        "✅ Пароль сохранён. Теперь можно входить в веб-кабинет по номеру в "
        f"Telegram и паролю:\n{url}"
    )
