"""FSM «отправить бриф»: выбор физлицо/ИП → ввод контакта → готовое приглашение.

Хендлеры тонкие: распознавание контакта и сборка приглашения — в сервисах ядра
(`services.contact`, `services.brief_invite`). Авто-отправка на email — будущая
доработка (нужен SMTP); пока бот отдаёт оператору текст для пересылки.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from config.settings import get_settings
from services.brief_invite import DeliveryChannel, compose_invite, delivery_channel
from services.brief_parser import BriefVariant
from services.contact import ContactParseError, detect_contact

from bot.access import OperatorOnly
from bot.keyboards import variant_keyboard
from bot.states import SendBrief

router = Router(name="send_brief")
router.message.filter(OperatorOnly())
router.callback_query.filter(OperatorOnly())

_CHANNEL_HINT = {
    DeliveryChannel.EMAIL: "Отправьте клиенту на email {value}:",
    DeliveryChannel.TELEGRAM: "Перешлите клиенту в Telegram {value}:",
    DeliveryChannel.MANUAL: "Отправьте клиенту по телефону {value}:",
}


@router.message(Command("send_brief"))
async def start_send_brief(message: Message, state: FSMContext) -> None:
    """Начать сценарий: спросить вариант брифа."""
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
    """Распознать контакт и выдать готовое приглашение с указанием канала."""
    try:
        contact = detect_contact(message.text or "")
    except ContactParseError:
        await message.answer("Не распознал контакт. Введите email, телефон или @username.")
        return

    data = await state.get_data()
    variant = BriefVariant(data["variant"])
    invite = compose_invite(variant, get_settings().public_base_url)
    channel = delivery_channel(contact)
    hint = _CHANNEL_HINT[channel].format(value=contact.value)

    await message.answer(f"{hint}\n\n{invite}")
    await state.clear()
