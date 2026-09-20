"""Нормализация хэштегов креатива (spec 2026-09-19-block1-remaining-gaps §B).

Оператор может дописать к тексту объявления хэштеги отдельным полем — здесь
единственное место, где строка хэштегов разбирается и проверяется. Приём
через API (`services/creative_intake.py`) переиспользует эти функции, чтобы
не заводить вторую реализацию правил в роутерах.
"""

from __future__ import annotations

import re

# Разделители тегов во входной строке: пробелы (включая переводы строк) и запятые.
_DELIMITER_RE = re.compile(r"[,\s]+")

# Допустимые символы тега: цифры, буквы кириллицы/латиницы, подчёркивание.
# `#` в начале — необязателен на входе, но всегда добавляется в результат.
_TAG_RE = re.compile(r"^#?[0-9A-Za-zА-Яа-яЁё_]+$")

MAX_TAGS = 10
MAX_TAG_LEN = 50


class HashtagError(Exception):
    """Проблема с хэштегами. `code`: `invalid_tag` | `too_many` | `tag_too_long` |
    `text_too_long` | `not_supported`. `detail` — уточнение для лога/отладки (сам
    проблемный тег, соотношение длины и лимита и т.п.), в HTTP-ответ не идёт —
    роутер отдаёт только машинный код (`f"hashtags_{code}"`)."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        self.code = code
        self.detail = detail
        super().__init__(code)


def normalize_hashtags(raw: str) -> list[str]:
    """Разобрать строку хэштегов в список нормализованных тегов (с `#`).

    Разделители — пробелы (в т.ч. переводы строк) и запятые. Пустая строка →
    пустой список. Дубликаты (без учёта регистра) отбрасываются, порядок первого
    вхождения сохраняется. Недопустимый символ в теге → `HashtagError
    ("invalid_tag")`, тег длиннее `MAX_TAG_LEN` символов (без `#`) →
    `HashtagError("tag_too_long")`, больше `MAX_TAGS` уникальных тегов →
    `HashtagError("too_many")`.
    """
    text = raw.strip()
    if not text:
        return []

    tags: list[str] = []
    seen: set[str] = set()
    for token in _DELIMITER_RE.split(text):
        if not token:
            continue
        if not _TAG_RE.match(token):
            raise HashtagError("invalid_tag", detail=token)
        tag = token if token.startswith("#") else f"#{token}"
        if len(tag) - 1 > MAX_TAG_LEN:
            raise HashtagError("tag_too_long", detail=tag)
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(tag)

    if len(tags) > MAX_TAGS:
        raise HashtagError("too_many", detail=str(len(tags)))
    return tags


def append_hashtags(body: str | None, tags: list[str], limit: int) -> str:
    """Дописать теги к тексту объявления отдельной строкой.

    Пустой список тегов — текст возвращается как есть (`None` → пустая строка).
    Итог длиннее `limit` символов текстового слота площадки → `HashtagError
    ("text_too_long")`.
    """
    if not tags:
        return body or ""
    suffix = " ".join(tags)
    result = f"{body}\n{suffix}" if body else suffix
    if len(result) > limit:
        raise HashtagError("text_too_long", detail=f"{len(result)}>{limit}")
    return result
