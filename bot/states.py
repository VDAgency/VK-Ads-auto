"""FSM-состояния бота."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class SendBrief(StatesGroup):
    """Сценарий «отправить бриф клиенту»."""

    choosing_variant = State()  # выбор: физлицо / ИП
    entering_contact = State()  # ввод контакта клиента


class LinkUserbot(StatesGroup):
    """Сценарий «подключить юзер-бота»: телефон → код → пароль 2FA (spec §9)."""

    entering_phone = State()  # ввод номера телефона Анастасии
    entering_code = State()  # ввод кода из Telegram
    entering_password = State()  # ввод пароля 2FA (если включён)
