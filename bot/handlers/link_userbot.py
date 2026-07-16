"""Подключение юзер-бота: телефон → код кнопками → пароль 2FA — spec §9.

Каждый оператор подключает СВОЮ сессию (`sender_id = from_user.id`).

Безопасность (§9):
- при пустом `USERBOT_BASE_URL` — мок-режим: показываем шаги, но честно говорим,
  что сервис не подключён (в БД/сеть ничего не уходит);
- **код набирается ТОЛЬКО кнопками** (инлайн-клавиатура): Telegram сканирует
  исходящие сообщения аккаунта и блокирует вход, если код был отправлен текстом
  (анти-фишинг) — даже верный. Callback-кнопки исходящих сообщений не создают.
  Текст, присланный на шаге кода, удаляем и предупреждаем;
- пароль 2FA вводится сообщением (Telegram перехватывает только коды входа,
  не облачные пароли) и сразу удаляется из чата;
- в логах маскируем код и пароль (`_redact`).
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import UserbotAuthError, UserbotUnavailable
from bot.keyboards import code_keypad
from bot.states import LinkUserbot

logger = logging.getLogger(__name__)

router = Router(name="link_userbot")
router.message.filter(OperatorOnly())
router.callback_query.filter(OperatorOnly())

_MOCK_NOTICE = (
    "🧪 Демо: юзер-бот ещё не подключён к серверу.\n"
    "Шаги авторизации показаны для ознакомления — реальная привязка появится "
    "после настройки сервиса доставки."
)
_ERROR_HINT = {
    "phone_code_invalid": "Неверный код. Попробуйте ещё раз командой /link_userbot.",
    "password_invalid": "Неверный пароль. Повторите: /link_userbot.",
}
_UNAVAILABLE = "Сервис юзербота недоступен, попробуйте позже."
_TEXT_CODE_WARNING = (
    "⚠️ Сообщение удалено. Код нельзя отправлять текстом: Telegram видит его в "
    "исходящем сообщении и блокирует вход, даже если код верный. Если вы "
    "отправили именно код — он уже не сработает: начните заново (/link_userbot) "
    "и наберите код кнопками."
)

# Код Telegram — 5 цифр; запас на будущее, но не даём набирать бесконечно.
_MAX_CODE_LEN = 8


def _redact(value: str) -> str:
    """Замаскировать секрет для логов: длину видно, содержимое — нет."""
    return f"<redacted:{len(value)}>"


def _code_prompt(code: str) -> str:
    """Текст над клавиатурой: инструкция + текущий набор."""
    shown = " ".join(code) if code else "—"
    return (
        "Код отправлен в Telegram (чат «Telegram», иногда SMS).\n"
        "⚠️ НЕ отправляйте код сообщением — Telegram заблокирует вход.\n"
        f"Наберите его кнопками ниже и нажмите «Готово».\n\nКод: {shown}"
    )


async def _delete_secret(message: Message) -> None:
    """Стереть сообщение с секретом из чата (§9). Ошибку удаления гасим — не критично."""
    try:
        await message.delete()
    except Exception:  # noqa: BLE001 — Telegram может запретить удаление, это не блокер
        logger.debug("link_userbot: message.delete() failed, continuing")


async def _edit_screen(callback: CallbackQuery, text: str, *, keypad: bool = False) -> None:
    """Обновить сообщение-экран набора; без клавиатуры набранный код исчезает из чата."""
    message = callback.message
    if message is None or isinstance(message, InaccessibleMessage):
        return
    await message.edit_text(text, reply_markup=code_keypad() if keypad else None)


@router.message(or_f(Command("link_userbot"), Command("link")))
async def start_link(message: Message, state: FSMContext) -> None:
    """Начать сценарий: показать статус СВОЕЙ сессии и запросить телефон (или мок)."""
    if message.from_user is None:
        return
    if not api_client.userbot_configured():
        await message.answer(_MOCK_NOTICE)
        await state.set_state(LinkUserbot.entering_phone)
        await message.answer("Демо-режим. Введите номер телефона (формат +7...):")
        return

    try:
        health = await api_client.userbot_status(message.from_user.id)
    except UserbotUnavailable:
        await message.answer(_UNAVAILABLE)
        return
    if health.authorized:
        await message.answer(f"Ваш юзер-бот уже подключён (номер {health.phone}).")
        return

    await state.set_state(LinkUserbot.entering_phone)
    await message.answer("Введите номер телефона вашего аккаунта (формат +7...):")


@router.message(LinkUserbot.entering_phone, F.text)
async def enter_phone(message: Message, state: FSMContext) -> None:
    """Принять телефон, запросить код и показать клавиатуру набора."""
    if message.from_user is None:
        return
    phone = (message.text or "").strip()
    if not api_client.userbot_configured():
        await state.update_data(phone=phone, code="")
        await state.set_state(LinkUserbot.entering_code)
        await message.answer(
            "Демо: код никуда не отправлялся. Наберите любые цифры кнопками:",
            reply_markup=code_keypad(),
        )
        return

    try:
        phone_code_hash = await api_client.userbot_start_auth(message.from_user.id, phone)
    except UserbotUnavailable:
        await state.clear()
        await message.answer(_UNAVAILABLE)
        return
    await state.update_data(phone=phone, phone_code_hash=phone_code_hash, code="")
    await state.set_state(LinkUserbot.entering_code)
    await message.answer(_code_prompt(""), reply_markup=code_keypad())


@router.message(LinkUserbot.entering_code, F.text)
async def code_typed_text(message: Message, state: FSMContext) -> None:
    """Текст на шаге кода: удалить и предупредить — такой код Telegram уже сжёг."""
    await _delete_secret(message)
    logger.info("link_userbot: text message during code entry deleted")
    await message.answer(_TEXT_CODE_WARNING)


@router.callback_query(StateFilter(LinkUserbot.entering_code), F.data.startswith("code:"))
async def code_button(callback: CallbackQuery, state: FSMContext) -> None:
    """Набор кода кнопками: цифра / стереть / готово / отмена."""
    parts = (callback.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""
    data = await state.get_data()
    code = str(data.get("code", ""))

    if action == "d" and len(parts) > 2:
        if len(code) >= _MAX_CODE_LEN:
            await callback.answer("Цифр уже достаточно — нажмите «Готово».")
            return
        code += parts[2]
        await state.update_data(code=code)
        await _edit_screen(callback, _code_prompt(code), keypad=True)
        await callback.answer()
        return

    if action == "del":
        if code:
            code = code[:-1]
            await state.update_data(code=code)
            await _edit_screen(callback, _code_prompt(code), keypad=True)
        await callback.answer()
        return

    if action == "cancel":
        await state.clear()
        await _edit_screen(callback, "Отменено. Начать заново: /link_userbot")
        await callback.answer()
        return

    if action == "ok":
        await _submit_code(callback, state, code)
        return

    await callback.answer()


async def _submit_code(callback: CallbackQuery, state: FSMContext, code: str) -> None:
    """Отправить набранный код в userbot-сервис; при 2FA — запросить пароль."""
    if not code:
        await callback.answer("Сначала наберите код кнопками.", show_alert=True)
        return
    logger.info("link_userbot: submitting code %s", _redact(code))

    if not api_client.userbot_configured():
        await state.clear()
        await _edit_screen(callback, _MOCK_NOTICE)
        await callback.answer()
        return

    data = await state.get_data()
    try:
        needs_password = await api_client.userbot_submit_code(
            callback.from_user.id, str(data["phone"]), code, str(data["phone_code_hash"])
        )
    except UserbotAuthError as exc:
        await state.clear()
        await _edit_screen(callback, _ERROR_HINT.get(exc.code, f"Ошибка авторизации: {exc.code}"))
        await callback.answer()
        return
    except UserbotUnavailable:
        await state.clear()
        await _edit_screen(callback, _UNAVAILABLE)
        await callback.answer()
        return

    if needs_password:
        await state.set_state(LinkUserbot.entering_password)
        await _edit_screen(
            callback, "Включена 2FA. Введите пароль сообщением (мы сразу удалим его из чата):"
        )
        await callback.answer()
        return
    await state.clear()
    await _edit_screen(callback, "✅ Юзер-бот подключён.")
    await callback.answer()


@router.message(LinkUserbot.entering_password, F.text)
async def enter_password(message: Message, state: FSMContext) -> None:
    """Принять пароль 2FA, удалить его из чата, завершить авторизацию."""
    if message.from_user is None:
        return
    password = (message.text or "").strip()
    await _delete_secret(message)
    logger.info("link_userbot: received password %s", _redact(password))

    if not api_client.userbot_configured():
        await state.clear()
        await message.answer(_MOCK_NOTICE)
        return

    try:
        await api_client.userbot_submit_password(message.from_user.id, password)
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
