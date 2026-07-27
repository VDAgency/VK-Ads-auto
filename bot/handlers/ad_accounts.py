"""Рекламные кабинеты в боте: список, добавление, проверка, удаление.

Спека: docs/superpowers/specs/2026-07-27-multi-cabinet-design.md §10.

Тонкий хендлер: собирает ввод и рендерит ответ, всё остальное — в ядре через
`bot/api_client` (CLAUDE.md §1.3). Токен уходит в ядро и обратно не приходит:
в списке видно только хвост из четырёх символов.

Безопасность (зеркало `/link_kotbot`): сообщение с токеном удаляется из чата
сразу после приёма, в логах вместо токена — длина.
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InaccessibleMessage, Message

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import AdAccountItem, AdAccountNotFound, AdAccountRejected, CoreUnavailable
from bot.keyboards import (
    ad_account_delete_confirm_keyboard,
    ad_account_kind_keyboard,
    ad_account_pick_keyboard,
    ad_accounts_keyboard,
)
from bot.states import AddAdAccount

logger = logging.getLogger(__name__)

router = Router(name="ad_accounts")
router.message.filter(OperatorOnly())
router.callback_query.filter(OperatorOnly())

_UNAVAILABLE = "Сервис временно недоступен, попробуйте позже."
_EMPTY = (
    "💼 <b>Рекламные кабинеты</b>\n\n"
    "Пока ни одного кабинета не добавлено.\n\n"
    "Кабинет нужен, чтобы запускать в нём кампании. Добавьте его — понадобится "
    "только <b>access_token</b> из VK Рекламы: название и номер кабинета "
    "подтянутся сами."
)
_ASK_KIND = (
    "Чью рекламу будете размещать в этом кабинете?\n\n"
    "Это нужно только для вашего удобства — чтобы не перепутать кабинеты. "
    "Маркировку (erid) VK проставляет сама."
)
_ASK_ADVERTISER = (
    "Введите конечного рекламодателя одной строкой: <b>название и ИНН</b>.\n"
    "Например: <code>ООО «Ромашка», 7701234567</code>"
)
_ASK_TOKEN = (
    "Пришлите <b>access_token</b> кабинета сообщением — я сразу удалю его из чата.\n\n"
    "Где взять: в VK Рекламе «Профиль» → «Доступ к API». Нужен именно "
    "<code>access_token</code> из выданного JSON."
)
_CANCELLED = "Отменено."

_HEALTH_LABEL = {
    "healthy": "✅ жив",
    "unauthorized": "⛔ токен не принят",
    "error": "⚠️ VK не ответил",
    "unknown": "… не проверялся",
}


def _redact(value: str) -> str:
    """Замаскировать секрет для логов: длину видно, содержимое — нет."""
    return f"<redacted:{len(value)}>"


async def _delete_secret(message: Message) -> None:
    """Стереть сообщение с токеном из чата. Отказ удаления гасим — не блокер."""
    try:
        await message.delete()
    except Exception:  # noqa: BLE001 — Telegram может запретить удаление
        logger.debug("ad_accounts: message.delete() failed, continuing")


def _account_line(item: AdAccountItem, index: int) -> str:
    """Одна карточка кабинета в списке."""
    health = _HEALTH_LABEL.get(item.health, item.health)
    tail = f"…{item.token_tail}" if item.token_tail else "—"
    lines = [
        f"<b>{index}. {item.title}</b>",
        f"    id {item.external_id} · токен {tail} · {health}",
    ]
    if item.health_error:
        lines.append(f"    {item.health_error}")
    if item.advertiser_kind == "third_party":
        advertiser = item.advertiser_name or "не указан"
        inn = f", ИНН {item.advertiser_inn}" if item.advertiser_inn else ""
        lines.append(f"    реклама третьего лица: {advertiser}{inn}")
    if item.balance_rub:
        lines.append(f"    баланс {item.balance_rub} ₽")
    return "\n".join(lines)


def _render(items: list[AdAccountItem]) -> str:
    if not items:
        return _EMPTY
    body = "\n\n".join(_account_line(item, i) for i, item in enumerate(items, start=1))
    return f"💼 <b>Рекламные кабинеты</b>\n\n{body}"


def _pick_items(items: list[AdAccountItem]) -> list[tuple[int, str]]:
    """Кабинеты для инлайн-выбора: (id строки, подпись с номером кабинета в VK)."""
    return [(item.id, f"{item.title} (id {item.external_id})") for item in items]


async def _show_list(message: Message) -> None:
    """Отрисовать список кабинетов с кнопками действий."""
    try:
        items = await api_client.list_ad_accounts()
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        return
    await message.answer(
        _render(items), parse_mode="HTML", reply_markup=ad_accounts_keyboard(bool(items))
    )


@router.message(Command("cabinets"))
async def cabinets_command(message: Message, state: FSMContext) -> None:
    """`/cabinets` — список рекламных кабинетов оператора."""
    await state.clear()
    await _show_list(message)


@router.callback_query(F.data == "adacc:cancel")
async def cancel(callback: CallbackQuery, state: FSMContext) -> None:
    """Отмена любого сценария кабинетов."""
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer(_CANCELLED)
    await callback.answer()


# --- добавление ---------------------------------------------------------------


@router.callback_query(F.data == "adacc:add")
async def start_add(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать добавление кабинета: сперва спрашиваем, чья это реклама."""
    await state.set_state(AddAdAccount.choosing_kind)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _ASK_KIND, parse_mode="HTML", reply_markup=ad_account_kind_keyboard()
        )
    await callback.answer()


@router.callback_query(F.data.startswith("adacckind:"), StateFilter(AddAdAccount.choosing_kind))
async def got_kind(callback: CallbackQuery, state: FSMContext) -> None:
    """Запомнить режим кабинета; для третьего лица спросить рекламодателя."""
    kind = (callback.data or "").split(":", 1)[1]
    await state.update_data(advertiser_kind=kind)
    if isinstance(callback.message, Message):
        if kind == "third_party":
            await state.set_state(AddAdAccount.entering_advertiser)
            await callback.message.answer(_ASK_ADVERTISER, parse_mode="HTML")
        else:
            await state.set_state(AddAdAccount.entering_token)
            await callback.message.answer(_ASK_TOKEN, parse_mode="HTML")
    await callback.answer()


@router.message(StateFilter(AddAdAccount.entering_advertiser))
async def got_advertiser(message: Message, state: FSMContext) -> None:
    """Разобрать «название, ИНН». ИНН — последнее число из 10–12 цифр, если есть."""
    raw = (message.text or "").strip()
    name, inn = raw, None
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) > 1 and parts[-1].isdigit() and 10 <= len(parts[-1]) <= 12:
        name, inn = ", ".join(parts[:-1]).strip(), parts[-1]
    await state.update_data(advertiser_name=name or None, advertiser_inn=inn)
    await state.set_state(AddAdAccount.entering_token)
    await message.answer(_ASK_TOKEN, parse_mode="HTML")


@router.message(StateFilter(AddAdAccount.entering_token))
async def got_token(message: Message, state: FSMContext) -> None:
    """Принять токен, немедленно стереть сообщение и завести кабинет через ядро."""
    token = (message.text or "").strip()
    await _delete_secret(message)
    if not token:
        await message.answer("Пустое сообщение — пришлите токен ещё раз.")
        return
    logger.info("ad account token received: %s", _redact(token))

    data = await state.get_data()
    await state.clear()
    try:
        item = await api_client.add_ad_account(
            token,
            advertiser_kind=str(data.get("advertiser_kind", "owner")),
            advertiser_name=data.get("advertiser_name"),
            advertiser_inn=data.get("advertiser_inn"),
        )
    except AdAccountRejected as exc:
        await message.answer(f"❌ {exc.reason}")
        return
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        return

    await message.answer(
        f"✅ Кабинет добавлен: <b>{item.title}</b>\n"
        f"id {item.external_id} · токен …{item.token_tail}",
        parse_mode="HTML",
    )
    await _show_list(message)


# --- проверка -----------------------------------------------------------------


@router.callback_query(F.data == "adacc:check")
async def start_check(callback: CallbackQuery) -> None:
    """Выбрать кабинет для принудительной проверки токена."""
    try:
        items = await api_client.list_ad_accounts()
    except CoreUnavailable:
        await callback.answer(_UNAVAILABLE, show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Какой кабинет проверить?",
            reply_markup=ad_account_pick_keyboard(_pick_items(items), "checkone"),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("adacc:checkone:"))
async def check_one(callback: CallbackQuery) -> None:
    """Проверить конкретный кабинет и показать свежий результат."""
    account_id = int((callback.data or "").rsplit(":", 1)[1])
    try:
        item = await api_client.check_ad_account(account_id)
    except AdAccountNotFound:
        await callback.answer("Кабинет уже удалён.", show_alert=True)
        return
    except CoreUnavailable:
        await callback.answer(_UNAVAILABLE, show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(_account_line(item, 1), parse_mode="HTML")
    await callback.answer()


# --- удаление -----------------------------------------------------------------


@router.callback_query(F.data == "adacc:del")
async def start_delete(callback: CallbackQuery) -> None:
    """Выбрать кабинет для удаления."""
    try:
        items = await api_client.list_ad_accounts()
    except CoreUnavailable:
        await callback.answer(_UNAVAILABLE, show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Какой кабинет удалить?",
            reply_markup=ad_account_pick_keyboard(_pick_items(items), "delpick"),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("adacc:delpick:"))
async def confirm_delete(callback: CallbackQuery) -> None:
    """Спросить подтверждение: токен стирается безвозвратно."""
    account_id = int((callback.data or "").rsplit(":", 1)[1])
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Удалить кабинет? Сохранённый токен будет стёрт безвозвратно.\n"
            "Кампании, запущенные в нём, останутся в отчётах.",
            reply_markup=ad_account_delete_confirm_keyboard(account_id),
        )
    await callback.answer()


@router.callback_query(F.data.startswith("adacc:delok:"))
async def do_delete(callback: CallbackQuery) -> None:
    """Удалить кабинет и показать обновлённый список."""
    account_id = int((callback.data or "").rsplit(":", 1)[1])
    try:
        await api_client.delete_ad_account(account_id)
    except AdAccountNotFound:
        await callback.answer("Кабинет уже удалён.", show_alert=True)
        return
    except CoreUnavailable:
        await callback.answer(_UNAVAILABLE, show_alert=True)
        return
    if isinstance(callback.message, Message) and not isinstance(
        callback.message, InaccessibleMessage
    ):
        await callback.message.answer("🗑 Кабинет удалён.")
        await _show_list(callback.message)
    await callback.answer()
