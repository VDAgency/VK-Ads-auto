"""Подключение юзер-бота (команда №1, каркас FSM) — spec §9.

Флоу: телефон → код → пароль 2FA (если включён). Полная боевая авторизация — в
блоке доставки; здесь собираем переходы и мок-режим.

Безопасность (§9):
- при пустом `USERBOT_BASE_URL` — мок-режим: показываем шаги, но честно говорим,
  что сервис не подключён (в БД/сеть ничего не уходит);
- после ввода кода и пароля удаляем сообщение оператора (`message.delete()`),
  чтобы секреты не оставались в истории чата;
- в логах маскируем код и пароль (`_redact`).
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import UserbotAuthError, UserbotUnavailable
from bot.states import LinkUserbot

logger = logging.getLogger(__name__)

router = Router(name="link_userbot")
router.message.filter(OperatorOnly())

_MOCK_NOTICE = (
    "🧪 Демо: юзер-бот ещё не подключён к серверу.\n"
    "Шаги авторизации показаны для ознакомления — реальная привязка появится "
    "после настройки сервиса доставки."
)
_ERROR_HINT = {
    "phone_code_invalid": "Неверный код. Попробуйте ещё раз командой /link_userbot.",
    "phone_code_expired": "Код истёк. Запросите новый: /link_userbot.",
    "password_hash_invalid": "Неверный пароль. Повторите: /link_userbot.",
}
_UNAVAILABLE = "Сервис юзербота недоступен, попробуйте позже."


def _redact(value: str) -> str:
    """Замаскировать секрет для логов: длину видно, содержимое — нет."""
    return f"<redacted:{len(value)}>"


async def _delete_secret(message: Message) -> None:
    """Стереть сообщение с секретом из чата (§9). Ошибку удаления гасим — не критично."""
    try:
        await message.delete()
    except Exception:  # noqa: BLE001 — Telegram может запретить удаление, это не блокер
        logger.debug("link_userbot: message.delete() failed, continuing")


@router.message(or_f(Command("link_userbot"), Command("link")))
async def start_link(message: Message, state: FSMContext) -> None:
    """Начать сценарий: показать статус и запросить телефон (или уйти в мок)."""
    if not api_client.userbot_configured():
        await message.answer(_MOCK_NOTICE)
        await state.set_state(LinkUserbot.entering_phone)
        await message.answer("Демо-режим. Введите номер телефона (формат +7...):")
        return

    try:
        health = await api_client.userbot_status()
    except UserbotUnavailable:
        await message.answer(_UNAVAILABLE)
        return
    if health.authorized:
        await message.answer(f"Юзер-бот уже подключён (номер {health.phone}).")
        return

    await state.set_state(LinkUserbot.entering_phone)
    await message.answer("Введите номер телефона юзер-бота (формат +7...):")


@router.message(LinkUserbot.entering_phone, F.text)
async def enter_phone(message: Message, state: FSMContext) -> None:
    """Принять телефон, запросить код (в мок-режиме — сымитировать)."""
    phone = (message.text or "").strip()
    if not api_client.userbot_configured():
        await state.update_data(phone=phone)
        await state.set_state(LinkUserbot.entering_code)
        await message.answer("Демо: код отправлен бы в Telegram. Введите любой код:")
        return

    try:
        phone_code_hash = await api_client.userbot_start_auth(phone)
    except UserbotUnavailable:
        await state.clear()
        await message.answer(_UNAVAILABLE)
        return
    await state.update_data(phone=phone, phone_code_hash=phone_code_hash)
    await state.set_state(LinkUserbot.entering_code)
    await message.answer("Код отправлен в Telegram. Введите его:")


@router.message(LinkUserbot.entering_code, F.text)
async def enter_code(message: Message, state: FSMContext) -> None:
    """Принять код, удалить его из чата; при 2FA — запросить пароль."""
    code = (message.text or "").strip()
    await _delete_secret(message)
    logger.info("link_userbot: received code %s", _redact(code))

    if not api_client.userbot_configured():
        await state.clear()
        await message.answer(_MOCK_NOTICE)
        return

    data = await state.get_data()
    try:
        needs_password = await api_client.userbot_submit_code(
            data["phone"], code, data["phone_code_hash"]
        )
    except UserbotAuthError as exc:
        await state.clear()
        await message.answer(_ERROR_HINT.get(exc.code, f"Ошибка авторизации: {exc.code}"))
        return
    except UserbotUnavailable:
        await state.clear()
        await message.answer(_UNAVAILABLE)
        return

    if needs_password:
        await state.set_state(LinkUserbot.entering_password)
        await message.answer("Включена 2FA. Введите пароль (сообщение удалим):")
        return
    await state.clear()
    await message.answer("✅ Юзер-бот подключён.")


@router.message(LinkUserbot.entering_password, F.text)
async def enter_password(message: Message, state: FSMContext) -> None:
    """Принять пароль 2FA, удалить его из чата, завершить авторизацию."""
    password = (message.text or "").strip()
    await _delete_secret(message)
    logger.info("link_userbot: received password %s", _redact(password))

    if not api_client.userbot_configured():
        await state.clear()
        await message.answer(_MOCK_NOTICE)
        return

    try:
        await api_client.userbot_submit_password(password)
    except UserbotAuthError as exc:
        await state.clear()
        await message.answer(_ERROR_HINT.get(exc.code, f"Ошибка авторизации: {exc.code}"))
        return
    except UserbotUnavailable:
        await state.clear()
        await message.answer(_UNAVAILABLE)
        return
    await state.clear()
    await message.answer("✅ Юзер-бот подключён.")
