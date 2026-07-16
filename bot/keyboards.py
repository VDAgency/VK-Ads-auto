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


def cabinets_keyboard(items: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    """Список кабинетов инлайн-кнопками. `items` = [(cabinet_id, подпись)]."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"cabinet:{cid}")]
            for cid, label in items
        ]
    )


def code_keypad() -> InlineKeyboardMarkup:
    """Цифровая клавиатура для кода авторизации юзер-бота (/link_userbot).

    Код входа НЕЛЬЗЯ отправлять сообщением: Telegram видит код в исходящем
    сообщении аккаунта и блокирует вход (анти-фишинг), даже если код верный.
    Кнопки передают цифры через callback_data — исходящих сообщений нет,
    вход не блокируется.
    """
    digit_rows = [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"]]
    rows = [
        [InlineKeyboardButton(text=digit, callback_data=f"code:d:{digit}") for digit in row]
        for row in digit_rows
    ]
    rows.append(
        [
            InlineKeyboardButton(text="⌫", callback_data="code:del"),
            InlineKeyboardButton(text="0", callback_data="code:d:0"),
            InlineKeyboardButton(text="✅ Готово", callback_data="code:ok"),
        ]
    )
    rows.append([InlineKeyboardButton(text="✖ Отмена", callback_data="code:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# Подписи периодов статистики (callback `stats:{cabinet_id}:{period}`).
_PERIOD_LABELS = [("all", "С запуска"), ("month", "Месяц"), ("week", "Неделя")]


def stats_period_keyboard(cabinet_id: str) -> InlineKeyboardMarkup:
    """Переключатель периода метрик кабинета."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text=label, callback_data=f"stats:{cabinet_id}:{period}")
                for period, label in _PERIOD_LABELS
            ]
        ]
    )
