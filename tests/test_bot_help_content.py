"""Инварианты содержимого справочника `/help`.

Контент правят часто, поэтому его проверки вынесены отдельно от логики навигации.
Главный тест здесь — `test_every_menu_command_is_documented`: он валит сборку, если
в синее меню добавили команду, а в справку — нет. Ровно из-за этого расхождения
старый статический `/help` и устарел.
"""

from __future__ import annotations

import re

import pytest
from bot import help_content
from bot.handlers.help import TELEGRAM_TEXT_LIMIT, render_menu, render_page
from bot.keyboards import help_page_cd
from bot.menu import bot_commands

# Запас до лимита Telegram: страница должна оставаться читаемой, а не влезать впритык.
PAGE_SOFT_LIMIT = 3000

# Лимит подписи инлайн-кнопки берём с запасом — длинная подпись обрезается Telegram.
BUTTON_LABEL_LIMIT = 30

# Ключ раздела попадает в callback_data, поэтому только латиница/цифры/подчёркивание.
_KEY_RE = re.compile(r"^[a-z0-9_]{1,12}$")

# Теги, которыми размечен контент. Всё остальное `<…>` — ошибка вёрстки: Telegram
# ответит «can't parse entities» и сообщение не отправится.
_ALLOWED_TAGS = ("b", "i", "u", "s", "code")
_TAG_RE = re.compile(r"</?([a-zA-Z]+)>")

# Контакты-заглушки, которые нельзя оставлять в проде.
_PLACEHOLDER_CONTACTS = ("@vdagency_support", "@example", "@support", "@username")

_SECTIONS = help_content.SECTIONS
_ALL_PAGES = [(section, index) for section in _SECTIONS for index in range(len(section.pages))]


def _all_text() -> str:
    """Весь текст справочника одной строкой — для проверок «упомянуто ли»."""
    return "\n".join(render_page(section, index) for section, index in _ALL_PAGES)


# --- Структура -----------------------------------------------------------------


def test_sections_and_pages_are_not_empty() -> None:
    assert 6 <= len(_SECTIONS) <= 8
    assert all(section.pages for section in _SECTIONS)
    assert 20 <= help_content.total_pages() <= 30


def test_section_keys_unique_and_slug_like() -> None:
    keys = [section.key for section in _SECTIONS]
    assert len(keys) == len(set(keys)), "ключи разделов должны быть уникальны"
    assert all(_KEY_RE.match(key) for key in keys)


def test_menu_items_match_sections() -> None:
    assert help_content.menu_items() == [(s.key, s.label) for s in _SECTIONS]


def test_next_section_walks_all_and_ends() -> None:
    visited = [_SECTIONS[0].key]
    following = help_content.next_section(_SECTIONS[0].key)
    while following is not None:
        visited.append(following.key)
        following = help_content.next_section(following.key)
    assert visited == [section.key for section in _SECTIONS]


def test_find_section_returns_none_for_unknown_key() -> None:
    assert help_content.find_section("nosuch") is None
    assert help_content.next_section("nosuch") is None


# --- Лимиты Telegram -----------------------------------------------------------


@pytest.mark.parametrize(("section", "page"), _ALL_PAGES, ids=lambda v: getattr(v, "key", v))
def test_page_fits_message_limit(section: help_content.HelpSection, page: int) -> None:
    text = render_page(section, page)
    assert len(text) <= PAGE_SOFT_LIMIT, f"{section.key}/{page + 1} слишком длинная"


def test_menu_fits_message_limit() -> None:
    assert len(render_menu()) <= TELEGRAM_TEXT_LIMIT


def test_callback_data_fits_telegram_limit() -> None:
    for section, page in _ALL_PAGES:
        assert len(help_page_cd(section.key, page).encode()) <= 64


def test_section_labels_fit_button() -> None:
    for section in _SECTIONS:
        assert len(section.label) <= BUTTON_LABEL_LIMIT, f"подпись «{section.label}» обрежется"


# --- Вёрстка -------------------------------------------------------------------


@pytest.mark.parametrize(("section", "page"), _ALL_PAGES, ids=lambda v: getattr(v, "key", v))
def test_html_tags_closed_within_single_line(section: help_content.HelpSection, page: int) -> None:
    """Тег открывается и закрывается в одной строке — иначе обрезка порвёт разметку."""
    for line in render_page(section, page).split("\n"):
        opened: list[str] = []
        for match in _TAG_RE.finditer(line):
            tag = match.group(1)
            if match.group(0).startswith("</"):
                assert opened and opened.pop() == tag, f"лишний </{tag}> в строке: {line}"
            else:
                opened.append(tag)
        assert not opened, f"незакрытый тег {opened} в строке: {line}"


@pytest.mark.parametrize(("section", "page"), _ALL_PAGES, ids=lambda v: getattr(v, "key", v))
def test_only_known_tags_used(section: help_content.HelpSection, page: int) -> None:
    text = render_page(section, page)
    assert all(tag in _ALLOWED_TAGS for tag in _TAG_RE.findall(text))
    # Голая угловая скобка вне тега сломает разбор HTML на стороне Telegram.
    assert "<" not in _TAG_RE.sub("", text)


# --- Полнота -------------------------------------------------------------------


def test_every_menu_command_is_documented() -> None:
    """Каждая команда синего меню разобрана в справке — защита от протухания."""
    text = _all_text()
    missing = [cmd.command for cmd in bot_commands() if f"/{cmd.command}" not in text]
    assert not missing, f"в справочнике нет разбора команд: {missing}"


def test_vk_primer_section_is_present() -> None:
    """Ликбез для оператора без опыта в VK Рекламе — обязательная часть справки."""
    primer = help_content.find_section("vkads")
    assert primer is not None
    body = " ".join(page.body for page in primer.pages).lower()
    for topic in ("кампани", "групп", "объявлени", "модерац", "erid", "бюджет", "ставк"):
        assert topic in body, f"в ликбезе не раскрыт «{topic}»"


def test_metrics_are_explained() -> None:
    """Оператор без опыта должен понять, что означают цифры статистики."""
    text = _all_text().lower()
    for metric in ("ctr", "cpc", "показы", "клики"):
        assert metric in text, f"нигде не объяснено «{metric}»"


def test_support_contact_is_set_and_reachable() -> None:
    # Аннотация снимает сужение до Literal — иначе mypy считает сравнение бессмысленным.
    contact: str = help_content.SUPPORT_CONTACT
    assert contact.startswith("@")
    assert contact not in _PLACEHOLDER_CONTACTS, "плейсхолдер не заменён на живой контакт"
    assert contact in _all_text()


def test_demo_data_is_flagged_honestly() -> None:
    """Демо-статистика помечена: обещать боевые цифры нельзя (CLAUDE.md §7)."""
    assert "демо-данные" in _all_text().lower()
