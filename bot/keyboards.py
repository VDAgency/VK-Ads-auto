"""Инлайн-клавиатуры бота."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


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
