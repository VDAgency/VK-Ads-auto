"""FSM-состояния бота."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class SendBrief(StatesGroup):
    """Сценарий «отправить бриф клиенту»."""

    choosing_variant = State()  # выбор: физлицо / ИП
    entering_contact = State()  # ввод контакта клиента
