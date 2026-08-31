"""Единый источник целей запуска кампании (перенос из `bot/handlers/creative.py`).

`launch_goals()` — список целей запуска (код, название, реализована ли) для всех
каналов (бот, веб-кабинет); `launch_goal_title()` — название по коду.
"""

from __future__ import annotations

from services.brief_parser import Goal
from services.goals import launch_goal_title, launch_goals


def test_launch_goals_lists_all_four_launch_time_goals() -> None:
    codes = [goal.code for goal in launch_goals()]
    assert codes == [
        Goal.SUBSCRIBERS.value,
        Goal.MESSAGES.value,
        Goal.LEAD_FORM.value,
        Goal.SENLER.value,
    ]


def test_launch_goals_are_all_implemented() -> None:
    """Сейчас реализованы все четыре цели — ни одна не «серая» в интерфейсах."""
    assert all(goal.implemented for goal in launch_goals())


def test_launch_goal_title_returns_human_title_by_code() -> None:
    assert launch_goal_title("subscribers") == "Подписчики"
    assert launch_goal_title("messages") == "Сообщения в сообщество"
    assert launch_goal_title("lead_form") == "Заявки — лид-форма"
    assert launch_goal_title("senler") == "Заявка через Senler"


def test_launch_goal_title_unknown_code_returns_as_is() -> None:
    assert launch_goal_title("future_goal") == "future_goal"
