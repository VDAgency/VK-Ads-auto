"""Профили целей кампании и их соответствие objective VK Ads.

На MVP активна одна цель — подписчики (`socialengagement`). Остальные цели
(сообщения, лид-форма, заявка через Senler) заложены в перечисление, но в
текущем объёме не запускаются (см. docs/ROADMAP.md, границы скоупа).
"""

from __future__ import annotations

from services.brief_parser import Goal

# Соответствие цели → VK objective. Сейчас поддержана только цель «подписчики».
GOAL_OBJECTIVE: dict[Goal, str] = {
    Goal.SUBSCRIBERS: "socialengagement",
}


def objective_for(goal: Goal) -> str:
    """VK objective для цели. Бросает `ValueError`, если цель не поддержана."""
    try:
        return GOAL_OBJECTIVE[goal]
    except KeyError as exc:
        raise ValueError(f"Unsupported goal: {goal}") from exc
