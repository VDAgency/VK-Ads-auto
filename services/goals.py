"""Профили целей кампании и площадок подписки.

Запускаются четыре цели — подписчики (`socialengagement`), заявки через лид-форму,
сообщения сообществу (`integrations.vk_surfaces.VK_MESSAGES`, боевой зонд 2026-08-23,
`verified=True`) и заявка через Senler (`integrations.vk_surfaces.VK_SENLER`, тот же
пакет VK, что и у «Сообщений»; собственный боевой прогон под именем Senler проведён
2026-08-24 — `verified=True`, площадка доступна клиенту наравне с остальными).

Зато сама цель «подписчики» ведёт не в одно место: подписаться можно на сообщество,
личную страницу, рассылку, канал VK, канал MAX и на два объекта в Одноклассниках.
Список площадок собирается здесь из справочника адаптера, чтобы бот и веб-кабинет
показывали одно и то же и не расходились между собой.
"""

from __future__ import annotations

from dataclasses import dataclass

from integrations.vk_surfaces import (
    GOAL_ENGAGEMENT,
    GOAL_LEADS,
    GOAL_MESSAGES,
    GOAL_SENLER,
    GOAL_SUBSCRIPTION,
    SURFACES,
)

from services.brief_parser import Goal, TargetType


def goal_for_target_type(target_type: TargetType) -> Goal:
    """Цель кампании по площадке брифа — единственное место, где решается это правило.

    Площадка «лид-форма» ведёт к сбору заявок, «сообщения» — к `Goal.MESSAGES`,
    «заявка через Senler» — к `Goal.SENLER` (обе прошли боевую проверку,
    `subscription_targets().available` для них истинно); все остальные площадки
    (включая смежные цели вовлечения — пост, музыка, клип) работают через цель
    «подписчики». `services.brief_parser.parse_brief` вызывает эту функцию вместо
    того, чтобы решать самому: правило не должно размазываться по модулям.
    """
    if target_type is TargetType.LEAD_FORM:
        return Goal.LEAD_FORM
    if target_type is TargetType.MESSAGES:
        return Goal.MESSAGES
    if target_type is TargetType.SENLER:
        return Goal.SENLER
    return Goal.SUBSCRIBERS


@dataclass(frozen=True)
class SubscriptionTarget:
    """Площадка подписки в виде, пригодном для интерфейсов: без деталей VK."""

    kind: str
    title: str
    hint: str
    available: bool
    # Цель кампании: подписка, вовлечение в готовый объект или сбор заявок.
    goal: str
    # Нужен ли клиенту креатив. Продвижение поста обходится без него: объявлением
    # служит сам пост, и просить у клиента картинку незачем.
    needs_creative: bool

    @property
    def target_type(self) -> TargetType:
        return TargetType(self.kind)


def subscription_targets() -> tuple[SubscriptionTarget, ...]:
    """Все площадки подписки для показа клиенту и оператору.

    `available` — прошла ли площадка боевую проверку. Непроверенную показываем, но
    выбрать не даём: так список остаётся честным и не приходится править интерфейсы
    при подключении очередной площадки.
    """
    return tuple(
        SubscriptionTarget(
            kind=surface.kind,
            title=surface.title,
            hint=surface.hint,
            available=surface.verified,
            goal=surface.goal,
            needs_creative=surface.needs_creative,
        )
        for surface in SURFACES
    )


def targets_for_goal(goal: str) -> tuple[SubscriptionTarget, ...]:
    """Площадки одной цели: подписка, вовлечение или заявки."""
    return tuple(target for target in subscription_targets() if target.goal == goal)


def goal_titles() -> dict[str, str]:
    """Человеческие названия целей для интерфейсов."""
    return {
        GOAL_SUBSCRIPTION: "Подписчики",
        GOAL_ENGAGEMENT: "Вовлечение в готовый объект",
        GOAL_LEADS: "Заявки через лид-форму",
        GOAL_MESSAGES: "Сообщения сообществу",
        GOAL_SENLER: "Заявка через Senler",
    }


def target_title(kind: str) -> str:
    """Человеческое название площадки по её ключу; неизвестный ключ — как есть."""
    for target in subscription_targets():
        if target.kind == kind:
            return target.title
    return kind


@dataclass(frozen=True)
class LaunchGoal:
    """Одна цель запуска кампании (`services.brief_parser.Goal`) для интерфейса
    выбора при загрузке креатива: код, название и реализована ли она.

    Не путать с `goal_titles()`/`GOAL_*` из `integrations.vk_surfaces` выше —
    это другой словарь (группировка площадок подписки по цели вовлечения), у
    него частично совпадающие, но не тождественные коды («leads» ≠ «lead_form»,
    «subscription» ≠ «subscribers»). `LaunchGoal` — про то, какую цель оператор
    явно выбирает при запуске кампании, и использует коды `Goal`.
    """

    code: str
    title: str
    implemented: bool


def launch_goals() -> tuple[LaunchGoal, ...]:
    """Цели запуска кампании — единый список для бота и веб-кабинета
    (CLAUDE.md §1.3: список не должен жить в обработчике канала).

    Нереализованная цель показывается в интерфейсе, но выбрать её нельзя — тот,
    кто рисует кнопку, обязан вести на отдельный алерт «скоро», а не подменять
    её молча. Сейчас реализованы все четыре: «Сообщения» прошли боевой зонд
    2026-08-23, «Заявка через Senler» — 2026-08-24 (оба `verified=True` в
    `integrations.vk_surfaces`).
    """
    return (
        LaunchGoal(Goal.SUBSCRIBERS.value, "Подписчики", True),
        LaunchGoal(Goal.MESSAGES.value, "Сообщения в сообщество", True),
        LaunchGoal(Goal.LEAD_FORM.value, "Заявки — лид-форма", True),
        LaunchGoal(Goal.SENLER.value, "Заявка через Senler", True),
    )


def launch_goal_title(code: str) -> str:
    """Человеческое название цели запуска по коду; неизвестный код — как есть."""
    for goal in launch_goals():
        if goal.code == code:
            return goal.title
    return code


# Правило: запуск без креатива всегда идёт под целью «подписчики» — объявлением
# служит сам пост, клип или трек, а ни одна площадка без креатива не относится к
# лид-форме/сообщениям/Senler (`goal_for_target_type` выше — этим трём целям
# отвечают только свои явные `target_type`). Отдаётся картой брифа
# (`BriefCardOut.launch_goal_title`), чтобы каналы не разбирали бриф сами
# (CLAUDE.md §1.3), а не решали это правило локально, как раньше.
NO_CREATIVE_GOAL: str = Goal.SUBSCRIBERS.value
