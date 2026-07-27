"""Справка по площадкам подписки (команда `/surfaces`).

Цель «подписчики» ведёт не в одно место: подписаться можно на сообщество, личную
страницу, рассылку, канал VK, канал MAX и на два объекта в Одноклассниках. Оператор
правит площадку в брифе полем «Куда привлекаем», и ему нужно знать, что туда писать —
иначе непонятое значение молча уедет как «сообщество».

Хендлер тонкий: список берётся из `services.goals`, своей логики здесь нет.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from services.goals import goal_titles, subscription_targets

from bot.access import OperatorOnly

router = Router(name="surfaces")
router.message.filter(OperatorOnly())

_INTRO = "🎯 <b>Куда можно вести рекламу</b>\n"
_OUTRO = (
    "\nПлощадка берётся из поля «Куда привлекаем» в брифе. Чтобы сменить её, откройте "
    "карточку брифа, нажмите «Внести правки» и отправьте номер этого поля с новым "
    "названием — например «8. рассылка»."
)


def render_surfaces() -> str:
    """Текст справки: площадки по целям, что писать в бриф и нужен ли креатив."""
    targets = subscription_targets()
    lines = [_INTRO]
    for goal, goal_title in goal_titles().items():
        group = [target for target in targets if target.goal == goal]
        if not group:
            continue
        lines.append(f"<b>— {goal_title} —</b>")
        for target in group:
            mark = "✅" if target.available else "🔜"
            # Где объявлением служит сам объект, картинку просить не нужно.
            creative = "" if target.needs_creative else "  (креатив не нужен)"
            lines.append(f"{mark} <b>{target.title}</b>{creative}")
            lines.append(f"   в бриф: «{target.kind}» или своими словами")
            lines.append(f"   {target.hint}")
        lines.append("")
    lines.append(_OUTRO)
    return "\n".join(lines)


@router.message(Command("surfaces"))
async def show_surfaces(message: Message) -> None:
    await message.answer(render_surfaces(), parse_mode="HTML")
