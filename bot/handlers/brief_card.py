"""Карточка полученного брифа: просмотр полей и правки `номер.значение`.

Тонкий хендлер: данные — через `api_client` (`GET/PATCH /briefs/{id}`), парсинг правок —
`services.edit_parser`, рендер здесь. Открывается по кнопке `brief:{id}` из списка
«Пришли за неделю» (см. pending.py); правки — по кнопке `edit:{id}` + ввод текстом.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from services.edit_parser import parse_edits

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import (
    BriefCard,
    BriefNotFound,
    CabinetChoiceRequired,
    CoreUnavailable,
    CreativeRejected,
)
from bot.keyboards import ad_account_pick_keyboard, brief_card_keyboard
from bot.states import EditBrief

router = Router(name="brief_card")
router.message.filter(OperatorOnly())
router.callback_query.filter(OperatorOnly())

_UNAVAILABLE = "Сервис временно недоступен, попробуйте позже."
_NOT_FOUND = "Бриф не найден."
_VARIANT_RU = {"individual": "физлицо", "community": "сообщество"}
_STATUS_RU = {
    "received": "принят",
    "prepared": "готов к запуску",
    "launched": "запущена",
    "failed": "ошибка запуска",
}
_EDIT_PROMPT = (
    "✏️ Отправьте правки в формате «номер.значение», каждую с новой строки.\n"
    "Например:\n1. Иван Петров\n7. Москва"
)
_EDIT_UNPARSED = (
    "Не понял правки. Формат: «номер.значение», каждая с новой строки.\nНапример:\n1. Иван Петров"
)
_ASK_CABINET = "В каком рекламном кабинете запускаем кампанию?"
_NO_CABINETS = (
    "⚠️ Ни одного рекламного кабинета не добавлено — запускать некуда.\n"
    "Добавьте кабинет командой /cabinets и повторите."
)
_NO_LIVE_CABINETS = (
    "⚠️ Ни один кабинет сейчас не годится: VK не принимает их токены.\n"
    "Откройте /cabinets, проверьте кабинеты и обновите токен."
)


def _render_card(card: BriefCard) -> str:
    """Текст карточки: заголовок + контакты клиента + нумерованные поля + статус креатива."""
    variant = _VARIANT_RU.get(card.variant, card.variant)
    status = _STATUS_RU.get(card.status, card.status)
    contacts = " · ".join(
        c for c in (card.client_email, card.client_phone, card.client_telegram) if c
    )
    who = card.client_name or "—"
    header_who = f"👤 {who}" + (f" — {contacts}" if contacts else "")
    lines = [f"📋 Бриф №{card.brief_id} · {variant} · {status}", header_who, ""]
    lines += [f"{field.n}. {field.label}: {field.value or '—'}" for field in card.fields]
    lines.append("")
    if card.surface_title:
        # Клиент пишет площадку словами; показываем, как её понял разбор, — ошибка
        # в поле видна до запуска, а не после.
        lines.append(f"🎯 Площадка: {card.surface_title} (список — /surfaces)")
    lines.append("🖼 Креатив: " + ("загружен" if card.has_creative else "не загружен"))
    return "\n".join(lines)


async def _send_card(message: Message, card: BriefCard) -> None:
    await message.answer(
        _render_card(card),
        reply_markup=brief_card_keyboard(card.brief_id, needs_creative=card.surface_needs_creative),
    )


async def _launch_and_report(message: Message, brief_id: int, ad_account_id: int | None) -> None:
    """Запустить кампанию без креатива выбранным кабинетом и показать итог оператору."""
    try:
        result = await api_client.launch_brief(brief_id, ad_account_id=ad_account_id)
    except BriefNotFound:
        await message.answer(_NOT_FOUND)
    except CabinetChoiceRequired as exc:
        # Ядро не смогло само выбрать кабинет (кабинетов нет либо их несколько) —
        # такое возможно, если состав кабинетов изменился прямо во время запуска.
        text = _NO_CABINETS if exc.reason == "no_ad_account" else _ASK_CABINET
        await message.answer(text)
    except CreativeRejected as exc:
        await message.answer(f"Запустить не вышло: {exc}")
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
    else:
        await message.answer(result.message)


@router.callback_query(F.data.startswith("launch:"))
async def launch_without_creative(callback: CallbackQuery) -> None:
    """Запустить кампанию для площадки, которой креатив не нужен.

    Объявлением служит сам пост, клип или трек — просить у оператора картинку,
    которая никуда не пойдёт, было бы выдумкой. Кабинет выбираем перед запуском
    точно так же, как при загрузке креатива (`bot/handlers/creative.py:start_creative`):
    единственный пригодный — берём сразу, несколько — спрашиваем оператора.
    """
    brief_id = int((callback.data or "").split(":", 1)[1])
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    message = callback.message

    try:
        accounts = await api_client.list_ad_accounts()
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        await callback.answer()
        return

    usable = [item for item in accounts if item.is_usable]
    if not accounts:
        await message.answer(_NO_CABINETS)
        await callback.answer()
        return
    if not usable:
        await message.answer(_NO_LIVE_CABINETS)
        await callback.answer()
        return

    if len(usable) == 1:
        # Один кабинет — выбирать не из чего, запускаем сразу.
        await _launch_and_report(message, brief_id, usable[0].id)
    else:
        await message.answer(
            _ASK_CABINET,
            reply_markup=ad_account_pick_keyboard(
                [(item.id, f"{item.title} (id {item.external_id})") for item in usable],
                # Свой action, отличный от `launch:{id}` в creative.py — иначе
                # выбор кабинета уедет в сценарий загрузки креатива (ловушка из ТЗ).
                f"nocre:{brief_id}",
            ),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("adacc:nocre:"))
async def picked_cabinet_for_launch(callback: CallbackQuery) -> None:
    """Оператор выбрал кабинет из нескольких — запускаем в нём."""
    parts = (callback.data or "").split(":")
    brief_id, ad_account_id = int(parts[2]), int(parts[3])
    if isinstance(callback.message, Message):
        await _launch_and_report(callback.message, brief_id, ad_account_id)
    await callback.answer()


@router.callback_query(F.data.startswith("brief:"))
async def open_brief(callback: CallbackQuery, state: FSMContext) -> None:
    """Открыть карточку брифа по кнопке из списка «Пришли за неделю»."""
    await state.clear()  # на случай прерванной правки
    brief_id = int((callback.data or "").split(":", 1)[1])
    if isinstance(callback.message, Message):
        try:
            card = await api_client.get_brief(brief_id)
        except BriefNotFound:
            await callback.message.answer(_NOT_FOUND)
        except CoreUnavailable:
            await callback.message.answer(_UNAVAILABLE)
        else:
            await _send_card(callback.message, card)
    await callback.answer()


@router.callback_query(F.data.startswith("edit:"))
async def start_edit(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать правку: запомнить бриф и попросить правки текстом."""
    brief_id = int((callback.data or "").split(":", 1)[1])
    await state.set_state(EditBrief.entering_edits)
    await state.update_data(brief_id=brief_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(_EDIT_PROMPT)
    await callback.answer()


@router.message(StateFilter(EditBrief.entering_edits))
async def apply_edits(message: Message, state: FSMContext) -> None:
    """Применить правки `номер.значение`: перезаписать в БД и показать обновлённую карточку."""
    parsed = parse_edits(message.text or "")
    if parsed.is_empty():
        # Ни одной валидной правки — остаёмся в состоянии, просим переввести.
        await message.answer(_EDIT_UNPARSED)
        return

    data = await state.get_data()
    brief_id = int(data["brief_id"])
    try:
        card, unknown = await api_client.update_brief(brief_id, parsed.edits)
    except BriefNotFound:
        await state.clear()
        await message.answer(_NOT_FOUND)
        return
    except CoreUnavailable:
        await state.clear()
        await message.answer(_UNAVAILABLE)
        return

    await state.clear()
    warnings: list[str] = []
    if parsed.invalid_lines:
        warnings.append("Не распознаны строки: " + "; ".join(parsed.invalid_lines))
    if unknown:
        warnings.append("Нет полей с номерами: " + ", ".join(str(n) for n in unknown))
    text = _render_card(card)
    if warnings:
        text += "\n\n⚠️ " + "\n".join(warnings)
    await message.answer(text, reply_markup=brief_card_keyboard(card.brief_id))
