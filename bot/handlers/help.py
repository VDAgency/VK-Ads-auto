"""Справочник `/help`: меню разделов и листаемые страницы в одном сообщении.

Навигация редактирует ОДНО сообщение (`edit_text`), а не шлёт новые: в чате всегда
ровно одна карточка помощи, история не засоряется и скролл не прыгает.

FSM здесь принципиально нет — позиция целиком лежит в `callback_data`:
- карточка листается и после рестарта бота (`MemoryStorage` состояние теряет);
- `/help` не затирает чужой незакрытый сценарий (ввод токена, контакта, креатива);
- две открытые карточки не делят одну позицию.

Контент — `bot/help_content.py`, развёрнутая версия — `docs/BOT_MANUAL.md`.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InaccessibleMessage, InlineKeyboardMarkup, Message

from bot import help_content
from bot.access import OperatorOnly
from bot.help_content import HelpSection
from bot.keyboards import (
    HELP_MENU_CD,
    HELP_PAGE_PREFIX,
    help_menu_keyboard,
    help_page_keyboard,
)

logger = logging.getLogger(__name__)

router = Router(name="help")
router.message.filter(OperatorOnly())
router.callback_query.filter(OperatorOnly())

# Лимит текста сообщения Telegram. Считается вместе с HTML-тегами.
TELEGRAM_TEXT_LIMIT = 4096

_STALE_HINT = "Справка обновилась — открыл меню разделов."


def _clip(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
    """Страховка от лимита Telegram: обрезать по границе строки.

    Резать можно только по переводу строки: теги в контенте не переносятся между
    строками (правило вёрстки `bot/help_content.py`), поэтому разметка не рвётся.
    """
    if len(text) <= limit:
        return text
    cut = text.rfind("\n", 0, limit)
    if cut <= 0:
        cut = limit
    return text[:cut].rstrip() + "\n…"


def render_menu() -> str:
    """Экран меню разделов."""
    return (
        "ℹ️ <b>Справочник по боту</b>\n"
        "\n"
        f"{len(help_content.SECTIONS)} разделов, {help_content.total_pages()} страниц. "
        "Внутри раздела листайте кнопками «◀ Назад» и «Далее ▶», кнопка «≡ В меню» "
        "вернёт сюда.\n"
        "\n"
        "Выберите раздел:"
    )


def render_page(section: HelpSection, page: int) -> str:
    """Страница раздела: шапка с позицией, заголовок, тело."""
    item = section.pages[page]
    header = f"{section.icon} <b>{section.title}</b> · {page + 1}/{len(section.pages)}"
    return _clip(f"{header}\n\n<b>{item.title}</b>\n\n{item.body}")


def parse_page_callback(data: str) -> tuple[HelpSection, int] | None:
    """Разобрать `help:s:{ключ}:{номер}`.

    `None` — мусор или неизвестный раздел (карточка от прошлой версии справки).
    Номер вне диапазона зажимается в границы раздела: ошибиться страницей — не
    повод показывать ошибку.
    """
    if not data.startswith(HELP_PAGE_PREFIX):
        return None
    key, _, raw_page = data[len(HELP_PAGE_PREFIX) :].partition(":")
    section = help_content.find_section(key)
    if section is None:
        return None
    try:
        page = int(raw_page)
    except ValueError:
        return None
    return section, max(0, min(page, len(section.pages) - 1))


async def _edit_card(callback: CallbackQuery, text: str, markup: InlineKeyboardMarkup) -> None:
    """Перерисовать карточку помощи на месте, не плодя сообщений."""
    message = callback.message
    if message is None or isinstance(message, InaccessibleMessage):
        return
    try:
        await message.edit_text(text, parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest as exc:
        if "not modified" in str(exc):
            # Повторное нажатие той же кнопки — не ошибка, молчим.
            return
        # Сообщение старше 48 часов или удалено: вместо мёртвой кнопки шлём новую
        # карточку — это честнее молчания.
        logger.info("help: не удалось отредактировать карточку (%s), отправляем новую", exc)
        await message.answer(text, parse_mode="HTML", reply_markup=markup)


async def _show_menu(callback: CallbackQuery) -> None:
    await _edit_card(callback, render_menu(), help_menu_keyboard(help_content.menu_items()))


@router.message(Command("help"))
async def show_help(message: Message) -> None:
    """`/help` — открыть меню разделов справочника новым сообщением."""
    await message.answer(
        render_menu(),
        parse_mode="HTML",
        reply_markup=help_menu_keyboard(help_content.menu_items()),
    )


@router.callback_query(F.data == HELP_MENU_CD)
async def back_to_menu(callback: CallbackQuery) -> None:
    """«≡ В меню» — вернуть карточку к списку разделов."""
    await _show_menu(callback)
    await callback.answer()


@router.callback_query(F.data.startswith(HELP_PAGE_PREFIX))
async def open_page(callback: CallbackQuery) -> None:
    """Открыть страницу раздела — единственная точка навигации внутри справки."""
    parsed = parse_page_callback(callback.data or "")
    if parsed is None:
        await _show_menu(callback)
        await callback.answer(_STALE_HINT)
        return
    section, page = parsed
    following = help_content.next_section(section.key)
    await _edit_card(
        callback,
        render_page(section, page),
        help_page_keyboard(
            section.key,
            page,
            len(section.pages),
            next_section=(following.key, following.title) if following is not None else None,
        ),
    )
    await callback.answer()
