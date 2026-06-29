"""Парсер правок сводки в формате `номер.значение` (Сценарий B, PROJECT.md §4.1.6).

Оператор присылает правки построчно, несколько за сообщение:

    1. подписчики
    5. 30000

Парсер возвращает отображение «номер поля → новое значение» и список нераспознанных
строк (чтобы бот мог переспросить). Сопоставление номера с конкретным параметром
кампании — ответственность карты сводки (services/mapping.py, Фаза 4), не парсера.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_EDIT_RE = re.compile(r"^\s*(\d+)\s*\.\s*(.+?)\s*$")


@dataclass(frozen=True)
class ParsedEdits:
    """Результат разбора. `edits`: номер → значение; `invalid_lines`: непонятые строки."""

    edits: dict[int, str] = field(default_factory=dict)
    invalid_lines: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        """True, если не распознано ни одной валидной правки."""
        return not self.edits


def parse_edits(message: str) -> ParsedEdits:
    """Разобрать многострочное сообщение с правками в формате `номер.значение`.

    Пустые строки игнорируются. При повторе номера побеждает последнее значение.
    Строки не по формату попадают в `invalid_lines`.
    """
    edits: dict[int, str] = {}
    invalid: list[str] = []
    for line in message.splitlines():
        if not line.strip():
            continue
        match = _EDIT_RE.match(line)
        if match is None:
            invalid.append(line.strip())
            continue
        edits[int(match.group(1))] = match.group(2).strip()
    return ParsedEdits(edits=edits, invalid_lines=invalid)
