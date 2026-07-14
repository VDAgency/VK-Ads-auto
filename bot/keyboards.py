"""Клавиатуры бота: постоянное reply-меню и инлайн-кнопки сценариев.

Тексты reply-кнопок вынесены в константы (`BTN_*`): их матчат хендлеры сценариев
(кнопка = та же точка входа, что и команда), поэтому они должны совпадать байт-в-байт.
"""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

# Постоянные reply-кнопки (всегда под полем ввода). Только частые действия;
# остальные команды — в синем нативном меню (см. bot/menu.py).
BTN_SEND_BRIEF = "📨 Отправить бриф"
BTN_PENDING = "📥 Ждём бриф"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Постоянная клавиатура с двумя частыми действиями."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_SEND_BRIEF), KeyboardButton(text=BTN_PENDING)]],
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="Выберите действие или введите команду",
    )


def variant_keyboard() -> InlineKeyboardMarkup:
    """Выбор варианта брифа: физлицо / ИП (бизнес)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Физлицо", callback_data="variant:individual"),
                InlineKeyboardButton(text="ИП / бизнес", callback_data="variant:community"),
            ]
        ]
    )
