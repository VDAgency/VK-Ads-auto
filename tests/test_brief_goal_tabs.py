"""Сверка вкладок «цель рекламы» брифа с бэкенд-справочником площадок.

Раньше обе формы брифа задавали один вопрос — «Куда привлекаем подписчиков?» —
и под ним единым списком лежали все 15 площадок: половина из них (лид-форма,
сообщения, пост с переходом на сайт) не имеет отношения к подписчикам вообще.
Теперь клиент выбирает цель вкладкой, а площадки внутри неё отфильтрованы по
полю `goal` каждой площадки (`web/lib/briefSurfaces.ts`), которое зеркалит
`integrations/vk_surfaces.py` — точку правды.

Полной автосинхронизации фронта и бэкенда нет (TypeScript и Python — разные
рантаймы), поэтому этот файл читает исходники `web/lib/*.ts` как текст и
сверяет их с `services.goals.subscription_targets()` — тем же справочником,
которым бот и веб-кабинет уже показывают площадку клиента одинаково
(`tests/test_surfaces_in_interfaces.py`). Расхождение (пропущенная площадка,
неверная группировка по цели, неверный флаг доступности) роняет эти тесты.
"""

from __future__ import annotations

import re
from pathlib import Path

from services.goals import subscription_targets

_WEB_LIB = Path(__file__).resolve().parent.parent / "web" / "lib"

_SURFACE_ENTRY_RE = re.compile(
    r'value:\s*"(?P<value>[^"]*)"\s*,\s*'
    r'label:\s*"(?P<label>[^"]*)"\s*,\s*'
    r"enabled:\s*(?P<enabled>true|false)\s*,\s*"
    r'goal:\s*"(?P<goal>[^"]*)"',
    re.DOTALL,
)

_TAB_ENTRY_RE = re.compile(
    r'key:\s*"(?P<key>[^"]*)"\s*,\s*'
    r'label:\s*"(?P<label>[^"]*)"\s*,\s*'
    r"enabled:\s*(?P<enabled>true|false)\s*,\s*"
    r'objectUrl:\s*\{\s*label:\s*"(?P<url_label>[^"]*)"\s*,\s*'
    r'hint:\s*"(?P<url_hint>[^"]*)"\s*,\s*'
    r'placeholder:\s*"(?P<url_placeholder>[^"]*)"',
    re.DOTALL,
)


def _strip_emoji(label: str) -> str:
    """Убрать эмодзи-префикс площадки: TS-подпись всегда «эмодзи пробел Название»."""
    return label.split(" ", 1)[1]


def _read_surfaces() -> list[re.Match[str]]:
    text = (_WEB_LIB / "briefSurfaces.ts").read_text(encoding="utf-8")
    entries = list(_SURFACE_ENTRY_RE.finditer(text))
    assert entries, "не удалось разобрать web/lib/briefSurfaces.ts — проверьте формат записей"
    return entries


def _read_tabs() -> list[re.Match[str]]:
    text = (_WEB_LIB / "briefGoals.ts").read_text(encoding="utf-8")
    entries = list(_TAB_ENTRY_RE.finditer(text))
    assert entries, "не удалось разобрать web/lib/briefGoals.ts — проверьте формат записей"
    return entries


def test_brief_surfaces_ts_matches_catalog() -> None:
    """Каждая площадка из TS совпадает с `services.goals.subscription_targets()`.

    Сверяются: заголовок (без эмодзи), доступность (`enabled`/`verified`) и
    цель (`goal`) — именно по ней вкладки группируют площадки. Расхождение
    здесь означает, что клиент увидит площадку не на той вкладке или вкладку
    без площадки, которая на самом деле есть.
    """
    targets = {target.kind: target for target in subscription_targets()}
    entries = _read_surfaces()
    assert len(entries) == len(targets), (
        f"web/lib/briefSurfaces.ts содержит {len(entries)} площадок, а справочник — {len(targets)}"
    )

    seen_values: set[str] = set()
    for entry in entries:
        value = entry["value"]
        assert value not in seen_values, f"площадка {value!r} продублирована в TS"
        seen_values.add(value)

        # Ищем совпадение по заголовку (без эмодзи), а не пытаемся угадать kind
        # из русской фразы `value` на стороне Python — сам разбор фразы уже
        # закреплён в services/brief_parser.py и его тестах.
        title = _strip_emoji(entry["label"])
        matches = [target for target in targets.values() if target.title == title]
        assert matches, f"площадка со заголовком {title!r} не найдена в справочнике"
        target = matches[0]

        assert (entry["enabled"] == "true") == target.available, (
            f"{title}: enabled в TS = {entry['enabled']}, "
            f"а available в справочнике = {target.available}"
        )
        assert entry["goal"] == target.goal, (
            f"{title}: goal в TS = {entry['goal']!r}, а в справочнике = {target.goal!r}"
        )


def test_surface_counts_per_goal_match_spec() -> None:
    """Числа площадок по целям — как в спеке 2026-08-23-brief-goal-tabs-design.md."""
    targets = subscription_targets()
    counts: dict[str, int] = {}
    for target in targets:
        counts[target.goal] = counts.get(target.goal, 0) + 1
    assert counts == {
        "subscription": 8,
        "engagement": 5,
        "leads": 1,
        "messages": 1,
    }


def test_goal_tabs_order_and_labels_match_spec() -> None:
    """Пять вкладок, порядок и подписи — из требования §1 спеки, не выдуманы."""
    tabs = _read_tabs()
    assert [tab["key"] for tab in tabs] == [
        "subscription",
        "engagement",
        "messages",
        "leads",
        "senler",
    ]
    assert [tab["label"] for tab in tabs] == [
        "Подписчики",
        "Вовлечение в готовый объект",
        "Сообщения сообществу",
        "Заявки — лид-форма",
        "Заявка через Senler",
    ]
    assert [tab["enabled"] for tab in tabs] == ["true", "true", "true", "true", "false"]


def test_goal_tab_keys_cover_every_catalog_goal() -> None:
    """Каждая цель из справочника площадок попадает в какую-то вкладку.

    Обратное (вкладка без единой площадки, кроме сознательно пустой Senler)
    проверяется тестом на количество площадок по целям.
    """
    catalog_goals = {target.goal for target in subscription_targets()}
    tab_keys = {tab["key"] for tab in _read_tabs()}
    assert catalog_goals <= tab_keys, f"вкладок не хватает для целей {catalog_goals - tab_keys}"


def test_senler_tab_has_no_object_url_copy_and_no_surfaces() -> None:
    """Senler — вкладка-обещание: в справочнике площадок её вообще нет.

    Подсказки поля «ссылка на объект» у неё пустые: клиент не может её выбрать
    и до этого поля не доберётся, выдумывать текст незачем.
    """
    tabs = {tab["key"]: tab for tab in _read_tabs()}
    senler = tabs["senler"]
    assert senler["enabled"] == "false"
    assert senler["url_label"] == ""
    assert senler["url_hint"] == ""
    catalog_goals = {target.goal for target in subscription_targets()}
    assert "senler" not in catalog_goals


def test_every_enabled_tab_has_object_url_copy() -> None:
    """Требование §4 спеки: подсказка и заголовок «ссылки на объект» — для каждой цели свои."""
    tabs = _read_tabs()
    enabled_tabs = [tab for tab in tabs if tab["enabled"] == "true"]
    assert len(enabled_tabs) == 4
    labels = [tab["url_label"] for tab in enabled_tabs]
    hints = [tab["url_hint"] for tab in enabled_tabs]
    for label, hint in zip(labels, hints, strict=True):
        assert label, "у доступной цели должен быть заголовок поля «ссылка на объект»"
        assert hint, "у доступной цели должна быть подсказка поля «ссылка на объект»"
    # Свои, а не одна и та же фраза, скопированная для всех целей.
    assert len(set(labels)) == len(labels)
    assert len(set(hints)) == len(hints)
