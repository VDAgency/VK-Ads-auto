"""FSM «отправить бриф»: выбор физлицо/ИП → контакт → создание инвайта в ядре.

Хендлеры тонкие: бот зовёт `POST /api/v1/invites` (вся логика — токен, запись
`BriefInvite`, доставка юзерботом/SMTP — на стороне ядра) и рендерит оператору
три сценария §8.1 спеки. Отправка в Telegram уходит от аккаунта вызвавшего
оператора (его сессия юзербота); перед стартом показываем баннер, если сессия
не подключена.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InaccessibleMessage, Message
from services.delivery.email import human_message as email_human_message
from services.delivery.telegram import human_message as telegram_human_message
from services.invites_fallback import EmailOffer, offer_email

from bot import api_client, userbot_watch
from bot.access import OperatorOnly
from bot.api_client import ContactNotRecognized, CoreUnavailable, InviteCreated
from bot.keyboards import BTN_SEND_BRIEF, email_fallback_keyboard, variant_keyboard
from bot.states import SendBrief

router = Router(name="send_brief")
router.message.filter(OperatorOnly())
router.callback_query.filter(OperatorOnly())

_CHANNEL_LABEL = {"telegram": "Telegram", "email": "email"}
_CORE_DOWN = "Сервис временно недоступен, попробуйте позже."


@router.message(or_f(Command("send_brief"), F.text == BTN_SEND_BRIEF))
async def start_send_brief(message: Message, state: FSMContext) -> None:
    """Начать сценарий: спросить вариант брифа (+ баннер, если сессия не привязана)."""
    user = message.from_user
    if user is not None:
        # Текст зависит от причины: «не подключён», «сеть не пускает» и «сервис лёг» —
        # разные ситуации с разными действиями оператора.
        banner = userbot_watch.banner_for(user.id)
        if banner is not None:
            await message.answer(banner)
    await state.set_state(SendBrief.choosing_variant)
    await message.answer("Кому отправляем бриф?", reply_markup=variant_keyboard())


@router.callback_query(StateFilter(SendBrief.choosing_variant), F.data.startswith("variant:"))
async def choose_variant(callback: CallbackQuery, state: FSMContext) -> None:
    """Запомнить вариант и попросить контакт клиента."""
    data = callback.data or ""
    await state.update_data(variant=data.split(":", 1)[1])
    await state.set_state(SendBrief.entering_contact)
    if isinstance(callback.message, Message):
        await callback.message.answer("Введите контакт клиента — email, телефон или @username:")
    await callback.answer()


@router.message(StateFilter(SendBrief.entering_contact))
async def got_contact(message: Message, state: FSMContext) -> None:
    """Создать инвайт в ядре и показать оператору итог доставки (§8.1)."""
    contact_raw = (message.text or "").strip()
    user = message.from_user
    if user is None:
        return

    data = await state.get_data()
    try:
        result = await api_client.create_invite(data["variant"], contact_raw, user.id)
    except ContactNotRecognized:
        # Остаёмся в состоянии — оператор вводит контакт заново.
        await message.answer("Не распознал контакт. Введите email, телефон или @username.")
        return
    except CoreUnavailable:
        await state.clear()
        await message.answer(_CORE_DOWN)
        return

    offer = offer_email(result.status, result.channel, result.error, result.fallback_email)
    if offer is None:
        await state.clear()
        await message.answer(_render_result(contact_raw, result))
        return
    await _offer_email_fallback(message, state, contact_raw, result, offer)


def _reply_target(callback: CallbackQuery) -> Message | None:
    """Сообщение, в чат которого отвечаем; `None` — карточка боту недоступна."""
    message = callback.message
    if message is None or isinstance(message, InaccessibleMessage):
        return None
    return message


async def _offer_email_fallback(
    message: Message,
    state: FSMContext,
    contact: str,
    result: InviteCreated,
    offer: EmailOffer,
) -> None:
    """Показать предложение отправить бриф письмом. Отправку начинает оператор."""
    await state.update_data(fallback_shown=True)
    text = _render_result(contact, result)
    if offer.email:
        await state.set_state(SendBrief.offering_email)
        await state.update_data(fallback_email=offer.email)
        await message.answer(
            f"{text}\n\n📧 У этого клиента есть email — можно отправить бриф письмом.",
            reply_markup=email_fallback_keyboard(offer.email),
        )
        return
    await state.set_state(SendBrief.entering_fallback_email)
    await message.answer(
        f"{text}\n\n📧 Если знаете email клиента — пришлите его, отправлю бриф письмом. "
        "Или просто перешлите текст выше вручную."
    )


@router.callback_query(StateFilter(SendBrief.offering_email), F.data == "invite_email")
async def send_to_known_email(callback: CallbackQuery, state: FSMContext) -> None:
    """Оператор согласился: создаём второй инвайт — уже на email клиента."""
    data = await state.get_data()
    message = _reply_target(callback)
    if message is not None:
        await _create_email_invite(
            message,
            state,
            str(data["variant"]),
            str(data["fallback_email"]),
            callback.from_user.id,
        )
    await callback.answer()


@router.callback_query(StateFilter(SendBrief.offering_email), F.data == "invite_cancel")
async def decline_email(callback: CallbackQuery, state: FSMContext) -> None:
    """Оператор отказался — оставляем текст для ручной пересылки."""
    await state.clear()
    message = _reply_target(callback)
    if message is not None:
        await message.answer("Хорошо, письмо не отправляю.")
    await callback.answer()


@router.message(StateFilter(SendBrief.entering_fallback_email))
async def got_fallback_email(message: Message, state: FSMContext) -> None:
    """Оператор прислал email вручную — отправляем бриф письмом."""
    user = message.from_user
    if user is None:
        return
    data = await state.get_data()
    await _create_email_invite(
        message, state, str(data["variant"]), (message.text or "").strip(), user.id
    )


async def _create_email_invite(
    message: Message, state: FSMContext, variant: str, email: str, operator_id: int
) -> None:
    """Второй инвайт на email. Канал выбирает ядро по типу контакта — как обычно."""
    try:
        result = await api_client.create_invite(variant, email, operator_id)
    except ContactNotRecognized:
        # Остаёмся в состоянии: оператор поправит адрес.
        await message.answer("Не похоже на email. Пришлите адрес вида ivan@mail.ru.")
        return
    except CoreUnavailable:
        await state.clear()
        await message.answer(_CORE_DOWN)
        return
    await state.clear()
    await message.answer(_render_result(email, result))


def _render_result(contact: str, result: InviteCreated) -> str:
    """Текст оператору по трём сценариям §8.1 спеки."""
    if result.status == "sent" and result.channel in _CHANNEL_LABEL:
        label = _CHANNEL_LABEL[result.channel]
        return f"✅ Отправлено клиенту {contact} через {label}. Ожидаем бриф."
    if result.status == "sent" and result.channel == "manual":
        return (
            "📞 Автоотправка на телефон невозможна. Перешлите клиенту вручную:\n\n"
            f"{result.fallback_text}"
        )
    reason = _failure_reason(result)
    return (
        f"⚠️ {reason} Проверьте контакт и попробуйте ещё раз (/send_brief), "
        f"либо отправьте вручную:\n\n{result.fallback_text}"
    )


def _failure_reason(result: InviteCreated) -> str:
    """Человекочитаемая причина отказа — словари каналов доставки."""
    code = result.error or ""
    if result.channel == "telegram":
        return telegram_human_message(code)
    if result.channel == "email":
        return email_human_message(code)
    return "Не удалось отправить."
