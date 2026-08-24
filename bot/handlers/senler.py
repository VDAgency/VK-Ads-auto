"""Привязка токена сообщества для проверки подключения Senler (B2, spec 2026-08-24 §7).

Токен — НЕ ключ API Senler (его проект сознательно не подключает, разведка
2026-08-24): это токен доступа к самому сообществу VK, которым проверяется
штатный метод `groups.getCallbackServers` — подключён ли к сообществу чат-бот
Senler. Выпускает администратор сообщества клиента в его настройках, привязан
к одному сообществу.

Тонкий хендлер: ввод и рендер здесь, всё остальное — в ядре через
`bot/api_client` (CLAUDE.md §1.3). Сообщение с токеном удаляется из чата сразу
после приёма — тот же приём, что в `/link_userbot` и `/cabinets`
(`bot/handlers/link_userbot.py`, `bot/handlers/ad_accounts.py`).
"""

from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import CommunityTokenRejected, CoreUnavailable
from bot.states import AddCommunityToken

logger = logging.getLogger(__name__)

router = Router(name="senler")
router.message.filter(OperatorOnly())

_ASK_COMMUNITY_ID = (
    "Введите числовой id сообщества VK (только цифры, без «club») — того самого, "
    "к которому клиент подключил чат-бота Senler.\n"
    "Найти его можно в адресе сообщества (vk.com/club<b>228817082</b>) или в его "
    "настройках."
)
_ASK_TOKEN = (
    "Пришлите токен доступа сообщества сообщением — я сразу удалю его из чата.\n\n"
    "Токен выпускает администратор сообщества в его настройках. Это не ключ API "
    "Senler — он не нужен."
)
_UNAVAILABLE = "Сервис временно недоступен, попробуйте позже."
_BAD_COMMUNITY_ID = "Нужны только цифры id сообщества. Попробуйте ещё раз."
_EMPTY_TOKEN = "Пустое сообщение — пришлите токен ещё раз."


def _redact(value: str) -> str:
    """Замаскировать секрет для логов: длину видно, содержимое — нет."""
    return f"<redacted:{len(value)}>"


async def _delete_secret(message: Message) -> None:
    """Стереть сообщение с токеном из чата. Отказ удаления гасим — не блокер."""
    try:
        await message.delete()
    except Exception:  # noqa: BLE001 — Telegram может запретить удаление
        logger.debug("senler: message.delete() failed, continuing")


@router.message(Command("senler_token"))
async def start(message: Message, state: FSMContext) -> None:
    """`/senler_token` — привязать токен сообщества к проверке подключения Senler."""
    await state.set_state(AddCommunityToken.entering_community_id)
    await message.answer(_ASK_COMMUNITY_ID, parse_mode="HTML")


@router.message(StateFilter(AddCommunityToken.entering_community_id))
async def got_community_id(message: Message, state: FSMContext) -> None:
    """Принять id сообщества (только цифры) и попросить токен."""
    raw = (message.text or "").strip()
    if not raw.isdigit():
        await message.answer(_BAD_COMMUNITY_ID)
        return
    await state.update_data(community_id=raw)
    await state.set_state(AddCommunityToken.entering_token)
    await message.answer(_ASK_TOKEN)


@router.message(StateFilter(AddCommunityToken.entering_token))
async def got_token(message: Message, state: FSMContext) -> None:
    """Принять токен, немедленно стереть сообщение и привязать через ядро."""
    token = (message.text or "").strip()
    await _delete_secret(message)
    if not token:
        await message.answer(_EMPTY_TOKEN)
        return
    logger.info("community token received: %s", _redact(token))

    data = await state.get_data()
    community_id = str(data.get("community_id", ""))
    await state.clear()

    try:
        result = await api_client.add_community_token(community_id, token)
    except CommunityTokenRejected as exc:
        await message.answer(f"❌ {exc.reason}")
        return
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        return

    if result.connected:
        await message.answer(
            f"✅ Токен сообщества {community_id} сохранён. Чат-бот Senler подключён — "
            "можно запускать кампанию с этой целью."
        )
    else:
        await message.answer(
            f"⚠️ Токен сообщества {community_id} сохранён, но подключение Senler не "
            f"подтвердилось: {result.reason}"
        )
