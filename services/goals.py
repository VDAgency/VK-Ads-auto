"""Профили целей кампании и площадок подписки.

Запускаются три цели — подписчики (`socialengagement`), заявки через лид-форму и
сообщения сообществу (`integrations.vk_surfaces.VK_MESSAGES`, боевой зонд 2026-08-23,
`verified=True`). Заявка через Senler в справочник площадок вообще не заведена
(см. docs/ROADMAP.md, границы скоупа).

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
    GOAL_SUBSCRIPTION,
    SURFACES,
)

from services.brief_parser import Goal, TargetType


def goal_for_target_type(target_type: TargetType) -> Goal:
    """Цель кампании по площадке брифа — единственное место, где решается это правило.

    Площадка «лид-форма» ведёт к сбору заявок, «сообщения» — к `Goal.MESSAGES`
    (площадка прошла боевую проверку, `subscription_targets().available` для неё
    истинно); все остальные площадки (включая смежные цели вовлечения — пост,
    музыка, клип) работают через цель «подписчики». `services.brief_parser.parse_brief`
    вызывает эту функцию вместо того, чтобы решать самому: правило не должно
    размазываться по модулям.
    """
    if target_type is TargetType.LEAD_FORM:
        return Goal.LEAD_FORM
    if target_type is TargetType.MESSAGES:
        return Goal.MESSAGES
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
    }


def target_title(kind: str) -> str:
    """Человеческое название площадки по её ключу; неизвестный ключ — как есть."""
    for target in subscription_targets():
        if target.kind == kind:
            return target.title
    return kind
