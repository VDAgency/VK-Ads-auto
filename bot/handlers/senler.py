"""Привязка токена сообщества для проверки подключения Senler (B2, spec 2026-08-24 §7).

Токен — НЕ ключ API Senler (его проект сознательно не подключает, разведка
2026-08-24): это токен доступа к самому сообществу VK, которым проверяется
штатный метод `groups.getCallbackServers` — подключён ли к сообществу чат-бот
Senler. Выпускает администратор сообщества клиента в его настройках, привязан
к одному сообществу.

Оператор вводит только сам токен — id сообщества называет сам VK
(`groups.getById` без `group_id`, `core/api/v1/senler.py`), так что сценарий
из одного шага, без риска перепутать сообщество.

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
from bot.api_client import CommunityTokenNotFound, CommunityTokenRejected, CoreUnavailable
from bot.states import AddCommunityToken, UnlinkCommunityToken

logger = logging.getLogger(__name__)

router = Router(name="senler")
router.message.filter(OperatorOnly())

_ASK_TOKEN = (
    "Пришлите токен доступа сообщества сообщением — я сразу удалю его из чата.\n\n"
    "Токен выпускает администратор сообщества в его настройках. Это не ключ API "
    "Senler — он не нужен. По токену я сам определю, к какому сообществу он "
    "относится."
)
_ASK_REFERENCE = (
    "Пришлите короткий адрес сообщества (то, что после vk.ru/) или его "
    "числовой id — привязанный к нему токен я отвяжу."
)
_UNAVAILABLE = "Сервис временно недоступен, попробуйте позже."
_EMPTY_TOKEN = "Пустое сообщение — пришлите токен ещё раз."
_EMPTY_REFERENCE = "Пустое сообщение — пришлите адрес или id ещё раз."
_NOT_FOUND = "Активной привязки для этого сообщества не было."


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
    await state.clear()

    try:
        result = await api_client.add_community_token(token)
    except CommunityTokenRejected as exc:
        await message.answer(f"❌ {exc.reason}")
        return
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        return

    if result.connected:
        await message.answer(
            f"✅ Токен сообщества «{result.community_name}» сохранён. Чат-бот Senler "
            "подключён — можно запускать кампанию с этой целью."
        )
    else:
        await message.answer(
            f"⚠️ Токен сообщества «{result.community_name}» сохранён, но подключение "
            f"Senler не подтвердилось: {result.reason}"
        )


@router.message(Command("senler_unlink"))
async def start_unlink(message: Message, state: FSMContext) -> None:
    """`/senler_unlink` — снять привязку токена сообщества (устаревшую или ошибочную).

    Без этой команды у оператора не было способа исправить дефект «два
    активных токена на один короткий адрес» (ревью 2026-08-24) иначе как
    правкой базы руками.
    """
    await state.set_state(UnlinkCommunityToken.entering_reference)
    await message.answer(_ASK_REFERENCE)


@router.message(StateFilter(UnlinkCommunityToken.entering_reference))
async def got_reference(message: Message, state: FSMContext) -> None:
    """Принять адрес/id и отвязать токен через ядро.

    Адрес сообщества — не секрет (в отличие от токена в `got_token`), поэтому
    сообщение из чата не стирается.
    """
    reference = (message.text or "").strip()
    await state.clear()
    if not reference:
        await message.answer(_EMPTY_REFERENCE)
        return

    try:
        await api_client.delete_community_token(reference)
    except CommunityTokenNotFound:
        await message.answer(f"ℹ️ {_NOT_FOUND}")
        return
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        return

    await message.answer(f"🗑 Привязка токена для «{reference}» снята.")
