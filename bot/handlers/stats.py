"""Статистика кабинетов (команда №4): список, вход в кабинет, переключение периода.

Тонкий хендлер: данные через `api_client`, рендер здесь. Демо-данные помечаются
баннером (флаг `is_mock` из ядра) — оператор всегда видит, что метрики не боевые.

Вход в кабинет и переключение периода (задача 2, дефект 1) сначала синкают метрики
ИМЕННО этого кабинета (`api_client.sync_cabinet_stats`), потом читают их из БД.
Экран всё равно показывается по сохранённым данным — разница только в пометке,
которую выбираем по исходу синка (A3, три состояния, классифицирует ядро в
`services.stats_sync.cabinet_sync_outcome`, бот только отображает готовый исход):
`"failed"` — честная тревожная пометка, что обновить не получилось (не имитируем
успех, CLAUDE.md §7); `"nothing_to_update"` — кампания просто не запущена, это
норма, пометка нейтральная, без ⚠️; `"updated"` — молча, пометки нет.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import CabinetItem, CabinetStats, CoreUnavailable
from bot.keyboards import cabinets_keyboard, stats_period_keyboard

router = Router(name="stats")
router.message.filter(OperatorOnly())
router.callback_query.filter(OperatorOnly())

_UNAVAILABLE = "Сервис временно недоступен, попробуйте позже."
_MOCK_BANNER = "⚠️ Демо-данные. Реальные появятся после подключения VK.\n\n"
_SYNC_FAILED_NOTE = "\n\n⚠️ Не удалось обновить данные, показаны последние сохранённые."
# «Нечего обновлять» (A3) — кампания не запущена (`prepared`/`stopped`/`failed`),
# синк площадку не спрашивал. Это НЕ сбой — пометка нейтральная, без ⚠️, иначе
# оператор увидит тревогу под каждым неподнятым кабинетом (баг, из-за которого A2
# перелечило исходную честную ошибку).
_SYNC_NOTHING_NOTE = "\n\nКампания не запущена — показаны итоговые цифры."
# Статус кабинета в списке — реальный статус кампании (A2, больше не захардкожен
# "active"): показываем его по-русски, не сырым кодом площадки. Значения — по
# `Campaign.status` (db/models.py), "active"/"paused" оставлены для обратной
# совместимости с более старыми записями канала.
_STATUS_HINT = {
    "prepared": "подготовлен",
    "launched": "запущен",
    "moderation": "на модерации",
    "stopped": "остановлен",
    "failed": "ошибка запуска",
    "active": "активен",
    "paused": "на паузе",
}
_PERIOD_HINT = {"all": "с запуска", "month": "за месяц", "week": "за неделю"}


def _render_list(cabinets: list[CabinetItem]) -> str:
    if not cabinets:
        return "Кабинетов пока нет. Появятся после запуска рекламы."
    header = _MOCK_BANNER if any(c.is_mock for c in cabinets) else ""
    lines = [f"{header}📊 Рекламные кабинеты ({len(cabinets)}):", ""]
    for cab in cabinets:
        status = _STATUS_HINT.get(cab.status, cab.status)
        lines.append(f"• {cab.name} — {status}")
    lines.append("")
    lines.append("Выберите кабинет для детальной статистики:")
    return "\n".join(lines)


def _render_stats(name: str, stats: CabinetStats) -> str:
    banner = _MOCK_BANNER if stats.is_mock else ""
    period = _PERIOD_HINT.get(stats.period, stats.period)
    return (
        f"{banner}📊 {name} — {period}\n\n"
        f"Показы: {int(stats.shows)}\n"
        f"Клики: {int(stats.clicks)}\n"
        f"Расход: {stats.spent:.0f} ₽\n"
        f"Результаты: {int(stats.results)}\n"
        f"CTR: {stats.ctr}%\n"
        f"CPC: {stats.cpc} ₽\n"
        f"CPL: {stats.cpl} ₽"
    )


@router.message(Command("stats"))
async def show_stats(message: Message) -> None:
    """Показать список кабинетов с кнопками входа."""
    try:
        cabinets = await api_client.get_cabinets()
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        return
    keyboard = cabinets_keyboard([(c.id, c.name) for c in cabinets]) if cabinets else None
    await message.answer(_render_list(cabinets), reply_markup=keyboard)


async def _answer_cabinet(message: Message, cabinet_id: str, period: str) -> None:
    """Обновить метрики кабинета и показать их. Синк не роняет показ (дефект 1).

    Пометка выбирается по готовому исходу синка (A3) — сам бот не решает, был ли
    сбой, только отображает то, что классифицировал сервисный слой.
    """
    outcome = await api_client.sync_cabinet_stats(cabinet_id)
    stats = await api_client.get_cabinet_stats(cabinet_id, period)
    text = _render_stats(cabinet_id, stats)
    if outcome == "failed":
        text += _SYNC_FAILED_NOTE
    elif outcome == "nothing_to_update":
        text += _SYNC_NOTHING_NOTE
    await message.answer(text, reply_markup=stats_period_keyboard(cabinet_id))


@router.callback_query(F.data.startswith("cabinet:"))
async def open_cabinet(callback: CallbackQuery) -> None:
    """Открыть кабинет — метрики с периодом по умолчанию (`all`)."""
    cabinet_id = (callback.data or "").split(":", 1)[1]
    if isinstance(callback.message, Message):
        try:
            await _answer_cabinet(callback.message, cabinet_id, "all")
        except CoreUnavailable:
            await callback.message.answer(_UNAVAILABLE)
    await callback.answer()


@router.callback_query(F.data.startswith("stats:"))
async def switch_period(callback: CallbackQuery) -> None:
    """Переключить период метрик кабинета (`stats:{id}:{period}`)."""
    _, cabinet_id, period = (callback.data or "").split(":", 2)
    if isinstance(callback.message, Message):
        try:
            await _answer_cabinet(callback.message, cabinet_id, period)
        except CoreUnavailable:
            await callback.message.answer(_UNAVAILABLE)
    await callback.answer()
