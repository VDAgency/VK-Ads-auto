"""Сверка вкладок «цель рекламы» брифа с бэкенд-справочником площадок.

Раньше обе формы брифа задавали один вопрос — «Куда привлекаем подписчиков?» —
и под ним единым списком лежали все 15 площадок: половина из них (лид-форма,
сообщения, пост с переходом на сайт) не имеет отношения к подписчикам вообще.
Теперь клиент выбирает цель вкладкой, а площадки внутри неё отфильтрованы по
полю `goal` каждой площадки (`web/lib/briefSurfaces.ts`), которое зеркалит
`integrations/vk_surfaces.py` — точку правды.

Доступность вкладки (`web/lib/briefGoals.ts::isGoalTabEnabled`) — не отдельный
флаг в TS-источнике, а производная: вкладка открыта, если у цели есть хотя бы
одна доступная площадка. Коммит ba87e54 захардкодил `enabled: true` для вкладки
Senler в обход этого правила — площадка внутри осталась заблокированной, и
клиент упирался в тупик (вкладка открыта, выбрать нечего). Тесты здесь и в
`tests/test_web_static.py` (сборка+рендер) закрепляют, что впредь такого
спецкейса по имени цели в коде быть не может: доступность каждой вкладки
проверяется правилом «есть хоть одна доступная площадка», а не именем.

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

from services.goals import subscription_targets, targets_for_goal

_WEB_LIB = Path(__file__).resolve().parent.parent / "web" / "lib"
_WEB_COMPONENTS = Path(__file__).resolve().parent.parent / "web" / "components"

_SURFACE_ENTRY_RE = re.compile(
    r'value:\s*"(?P<value>[^"]*)"\s*,\s*'
    r'label:\s*"(?P<label>[^"]*)"\s*,\s*'
    r"enabled:\s*(?P<enabled>true|false)\s*,\s*"
    r'goal:\s*"(?P<goal>[^"]*)"',
    re.DOTALL,
)

# Табы больше не несут собственный флаг `enabled` — доступность вычисляется из
# площадок (см. модульный докстринг), поэтому регулярка разбирает только
# ключ/подпись/подсказки поля «ссылка на объект».
_TAB_ENTRY_RE = re.compile(
    r'key:\s*"(?P<key>[^"]*)"\s*,\s*'
    r'label:\s*"(?P<label>[^"]*)"\s*,\s*'
    r'objectUrl:\s*\{\s*label:\s*"(?P<url_label>[^"]*)"\s*,\s*'
    r'hint:\s*"(?P<url_hint>[^"]*)"\s*,\s*'
    r'placeholder:\s*"(?P<url_placeholder>[^"]*)"',
    re.DOTALL,
)

_TAB_ARRAY_RE = re.compile(
    r"export const BRIEF_GOAL_TABS: BriefGoalTab\[\] = \[(?P<body>.*?)\n\];", re.DOTALL
)


def _strip_emoji(label: str) -> str:
    """Убрать эмодзи-префикс площадки: TS-подпись всегда «эмодзи пробел Название»."""
    return label.split(" ", 1)[1]


def _briefgoals_text() -> str:
    return (_WEB_LIB / "briefGoals.ts").read_text(encoding="utf-8")


def _read_surfaces() -> list[re.Match[str]]:
    text = (_WEB_LIB / "briefSurfaces.ts").read_text(encoding="utf-8")
    entries = list(_SURFACE_ENTRY_RE.finditer(text))
    assert entries, "не удалось разобрать web/lib/briefSurfaces.ts — проверьте формат записей"
    return entries


def _read_tabs() -> list[re.Match[str]]:
    entries = list(_TAB_ENTRY_RE.finditer(_briefgoals_text()))
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
        "senler": 1,
    }


def test_goal_tabs_order_and_labels_match_spec() -> None:
    """Пять вкладок, порядок и подписи — из требования §1 спеки, не выдуманы.

    Доступность вкладки в исходнике больше не хранится (см. модульный
    докстринг) — она вычисляется из площадок цели, поэтому здесь не
    проверяется; поведение (какие вкладки реально открыты в собранном HTML)
    закрепляют `test_senler_tab_has_no_available_surface`/
    `test_engagement_tab_keeps_an_available_surface` ниже и
    `tests/test_web_static.py`.
    """
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


def test_goal_tab_keys_cover_every_catalog_goal() -> None:
    """Каждая цель из справочника площадок попадает в какую-то вкладку."""
    catalog_goals = {target.goal for target in subscription_targets()}
    tab_keys = {tab["key"] for tab in _read_tabs()}
    assert catalog_goals <= tab_keys, f"вкладок не хватает для целей {catalog_goals - tab_keys}"


def test_goal_tabs_have_no_hardcoded_enabled_flag() -> None:
    """Регрессия на коммит ba87e54: у вкладки не должно быть своего поля `enabled`.

    Хардкод `enabled: true` для вкладки Senler в обход площадок внутри неё и был
    причиной бага (вкладка открыта, единственная площадка внутри — заблокирована,
    клиент упирается в тупик). Проверяем именно текст массива `BRIEF_GOAL_TABS`
    (а не весь файл — там законно есть слово «enabled» в имени/докстринге
    функции `isGoalTabEnabled`, вычисляющей доступность из площадок).
    """
    match = _TAB_ARRAY_RE.search(_briefgoals_text())
    assert match, "не удалось найти массив BRIEF_GOAL_TABS в web/lib/briefGoals.ts"
    assert "enabled" not in match["body"], (
        "у вкладки не должно быть собственного поля enabled — доступность обязана "
        "вычисляться из площадок цели (см. isGoalTabEnabled), без хардкода по вкладке"
    )


def test_goal_surface_component_has_no_senler_special_case() -> None:
    """Регрессия: в компоненте не должно быть логики, завязанной на имя «senler».

    Общее правило («вкладка доступна, если доступна хоть одна её площадка»)
    не требует знать имена целей — компонент оперирует только `enabled` полей
    площадок. Появление строки «senler» здесь означало бы возврат
    списка-исключения вместо общего правила.
    """
    text = (_WEB_COMPONENTS / "BriefGoalSurface.tsx").read_text(encoding="utf-8")
    assert "senler" not in text.lower()


def test_senler_tab_now_has_an_available_surface() -> None:
    """Решение 2026-08-24: Senler — реализованная цель (раскладка и запуск её
    принимают, tests/test_senler_goal.py), в справочнике площадок у неё один
    элемент. Собственный боевой прогон под именем Senler руководитель провёл
    в тот же день — `integrations.vk_surfaces.VK_SENLER.verified` стал `True`.

    По общему правилу (`isGoalTabEnabled`) это значит, что сама вкладка обязана
    рендериться как открытая — не потому, что где-то в коде написано имя
    «senler», а потому, что у цели теперь есть доступная площадка. Поведение в
    собранном HTML проверяет `tests/test_web_static.py`. Такое же правило,
    примененное к клипу ВК (площадка, которая всё ещё не прошла проверку),
    закрепляет `test_engagement_tab_keeps_an_available_surface` ниже: у той
    цели вкладка остаётся открытой, а недоступна только одна площадка внутри.
    """
    targets = {target.kind: target for target in subscription_targets()}
    senler_targets = [target for target in targets.values() if target.goal == "senler"]
    assert [target.kind for target in senler_targets] == ["senler"]
    assert senler_targets[0].available is True

    assert any(target.available for target in targets_for_goal("senler")), (
        "у цели senler должна быть доступная площадка — иначе вкладка обязана остаться закрытой"
    )


def test_engagement_tab_keeps_an_available_surface() -> None:
    """«Вовлечение в готовый объект» не должна пострадать от общего правила.

    У цели заблокирована площадка «клип» (verified=False), но остальные
    четыре (пост сообщества, пост личной страницы, пост со ссылкой на сайт,
    музыка) доступны — по общему правилу вкладка обязана остаться открытой.
    """
    engagement_targets = targets_for_goal("engagement")
    assert len(engagement_targets) == 5
    assert any(target.available for target in engagement_targets), (
        "у цели engagement должна остаться хотя бы одна доступная площадка — "
        "иначе вкладка «Вовлечение» стала бы недоступна вслед за клипом"
    )
    unavailable = [target.kind for target in engagement_targets if not target.available]
    assert unavailable == ["vk_clip"], (
        f"ожидали заблокированным только клип, получили {unavailable}"
    )


def test_every_goal_tab_has_object_url_copy() -> None:
    """Требование §4 спеки: подсказка и заголовок «ссылки на объект» — для каждой цели свои.

    Копия статична и не зависит от текущей доступности вкладки — заблокированная
    сегодня вкладка обязана прийти со своими текстами уже готовой к тому дню,
    когда её площадка пройдёт боевой прогон и вкладка откроется сама.
    """
    tabs = _read_tabs()
    assert len(tabs) == 5
    labels = [tab["url_label"] for tab in tabs]
    hints = [tab["url_hint"] for tab in tabs]
    for label, hint in zip(labels, hints, strict=True):
        assert label, "у цели должен быть заголовок поля «ссылка на объект»"
        assert hint, "у цели должна быть подсказка поля «ссылка на объект»"
    # Свои, а не одна и та же фраза, скопированная для всех целей.
    assert len(set(labels)) == len(labels)
    assert len(set(hints)) == len(hints)
