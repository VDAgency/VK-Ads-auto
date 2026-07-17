"""Подключение kotbot: стратегия → логин → пароль → код — spec 2026-07-17 §5.

kotbot.ru не имеет API: вход выполняет сервис kotbot/ браузером (Playwright,
K-PR3). Здесь — только диалог с оператором.

Безопасность (§5):
- пароль и код подтверждения вводятся сообщениями и СРАЗУ удаляются из чата
  (`_delete_secret`); коды kotbot/VK — не телеграмные, кейпад не нужен;
- в логах секреты маскируются (`_redact`);
- при пустом `KOTBOT_BASE_URL` — мок-режим: показываем шаги, но честно говорим,
  что сервис не подключён (в сеть ничего не уходит).
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import KotbotAuthError, KotbotUnavailable
from bot.keyboards import kotbot_strategy_keyboard
from bot.states import LinkKotbot

logger = logging.getLogger(__name__)

router = Router(name="link_kotbot")
router.message.filter(OperatorOnly())
router.callback_query.filter(OperatorOnly())

_MOCK_NOTICE = (
    "🧪 Демо: сервис kotbot-автоматизации ещё не подключён.\n"
    "Шаги авторизации показаны для ознакомления — реальная привязка появится "
    "после настройки сервиса (KOTBOT_BASE_URL)."
)
_UNAVAILABLE = "Сервис kotbot-автоматизации недоступен, попробуйте позже."
_ERROR_HINT = {
    "invalid_credentials": "Неверный логин или пароль. Повторите: /link_kotbot.",
    "code_invalid": "Неверный код. Попробуйте ещё раз: /link_kotbot.",
    "attempt_expired": "Время вышло — начните заново /link_kotbot.",
    "captcha_required": (
        "kotbot/VK показывает капчу — войдите вручную в браузере, затем повторите /link_kotbot."
    ),
    "not_configured": (
        "Сервис kotbot не настроен на сервере (нет ключа шифрования KOTBOT_SECRET_KEY)."
    ),
    "not_implemented": "Браузерная автоматизация ещё не выкачена (K-PR3) — подключение позже.",
}
_STRATEGY_LABELS = {"email": "почта и пароль", "vk": "VK-аккаунт"}
_LOGIN_PROMPTS = {
    "email": "Введите почту, под которой входите на kotbot.ru:",
    "vk": "Введите логин VK (телефон или почту):",
}
_PASSWORD_PROMPT = "Введите пароль сообщением (мы сразу удалим его из чата):"
_CODE_PROMPT = "Введите код подтверждения сообщением (мы сразу удалим его из чата):"


def _redact(value: str) -> str:
    """Замаскировать секрет для логов: длину видно, содержимое — нет."""
    return f"<redacted:{len(value)}>"


def _hint(code: str) -> str:
    """Человеческая подсказка по машинному коду ошибки kotbot-сервиса."""
    return _ERROR_HINT.get(code, f"Ошибка авторизации: {code}")


def _success_text(strategy: str) -> str:
    return f"✅ kotbot подключён (стратегия: {_STRATEGY_LABELS.get(strategy, strategy)})."


async def _delete_secret(message: Message) -> None:
    """Стереть сообщение с секретом из чата (§5). Ошибку удаления гасим — не критично."""
    try:
        await message.delete()
    except Exception:  # noqa: BLE001 — Telegram может запретить удаление, это не блокер
        logger.debug("link_kotbot: message.delete() failed, continuing")


async def _edit_screen(callback: CallbackQuery, text: str) -> None:
    """Обновить сообщение-экран сценария (кнопки после выбора убираем)."""
    message = callback.message
    if message is None or isinstance(message, InaccessibleMessage):
        return
    await message.edit_text(text, reply_markup=None)


@router.message(or_f(Command("link_kotbot"), Command("kotbot")))
async def start_link(message: Message, state: FSMContext) -> None:
    """Начать сценарий: предупредить о мок-режиме и показать выбор стратегии."""
    if message.from_user is None:
        return
    if not api_client.kotbot_configured():
        await message.answer(_MOCK_NOTICE)
    await state.set_state(LinkKotbot.choosing_strategy)
    await message.answer("Как входить на kotbot.ru?", reply_markup=kotbot_strategy_keyboard())


@router.callback_query(StateFilter(LinkKotbot.choosing_strategy), F.data.startswith("kotbot:"))
async def choose_strategy(callback: CallbackQuery, state: FSMContext) -> None:
    """Выбор стратегии кнопкой: почта / VK / отмена."""
    parts = (callback.data or "").split(":")
    action = parts[1] if len(parts) > 1 else ""

    if action == "cancel":
        await state.clear()
        await _edit_screen(callback, "Отменено. Начать заново: /link_kotbot")
        await callback.answer()
        return

    if action not in _STRATEGY_LABELS:
        await callback.answer()
        return

    await state.update_data(strategy=action)
    await state.set_state(LinkKotbot.entering_login)
    await _edit_screen(callback, _LOGIN_PROMPTS[action])
    await callback.answer()


@router.message(LinkKotbot.entering_login, F.text)
async def enter_login(message: Message, state: FSMContext) -> None:
    """Принять логин, запросить пароль."""
    login = (message.text or "").strip()
    await state.update_data(login=login)
    await state.set_state(LinkKotbot.entering_password)
    await message.answer(_PASSWORD_PROMPT)


@router.message(LinkKotbot.entering_password, F.text)
async def enter_password(message: Message, state: FSMContext) -> None:
    """Принять пароль, удалить его из чата, начать вход через kotbot-сервис."""
    password = (message.text or "").strip()
    await _delete_secret(message)
    logger.info("link_kotbot: received password %s", _redact(password))

    if not api_client.kotbot_configured():
        await state.clear()
        await message.answer(_MOCK_NOTICE)
        return

    data = await state.get_data()
    strategy = str(data.get("strategy", "email"))
    login = str(data.get("login", ""))
    try:
        result = await api_client.kotbot_start_auth(strategy, login, password)
    except KotbotAuthError as exc:
        await state.clear()
        await message.answer(_hint(exc.code))
        return
    except KotbotUnavailable:
        await state.clear()
        await message.answer(_UNAVAILABLE)
        return

    if result.status == "code_required":
        await state.update_data(attempt_id=result.attempt_id or "")
        await state.set_state(LinkKotbot.entering_code)
        prompt = f"{result.hint}\n{_CODE_PROMPT}" if result.hint else _CODE_PROMPT
        await message.answer(prompt)
        return

    await state.clear()
    await message.answer(_success_text(strategy))


@router.message(LinkKotbot.entering_code, F.text)
async def enter_code(message: Message, state: FSMContext) -> None:
    """Принять код подтверждения, удалить его из чата, завершить вход."""
    code = (message.text or "").strip()
    await _delete_secret(message)
    logger.info("link_kotbot: received confirmation code %s", _redact(code))

    if not api_client.kotbot_configured():
        await state.clear()
        await message.answer(_MOCK_NOTICE)
        return

    data = await state.get_data()
    attempt_id = str(data.get("attempt_id", ""))
    strategy = str(data.get("strategy", "email"))
    try:
        await api_client.kotbot_submit_code(attempt_id, code)
    except KotbotAuthError as exc:
        await state.clear()
        await message.answer(_hint(exc.code))
        return
    except KotbotUnavailable:
        await state.clear()
        await message.answer(_UNAVAILABLE)
        return

    await state.clear()
    await message.answer(_success_text(strategy))
