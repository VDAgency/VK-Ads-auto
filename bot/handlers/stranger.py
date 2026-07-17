"""Визитка для не-операторов: бот приватный, за запуском рекламы — к Анастасии.

Регистрируется последним роутером (после всех операторских под `OperatorOnly`),
поэтому ловит только апдейты от чужих: любое сообщение → визитка + переход в личку.
"""

from __future__ import annotations

from aiogram import Router
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.access import NonOperator

router = Router(name="stranger")
router.message.filter(NonOperator())

_CONTACT_USERNAME = "zhuknastya"
_CONTACT_URL = f"https://t.me/{_CONTACT_USERNAME}"

_MESSAGE = (
    "👋 <b>Здравствуйте!</b>\n\n"
    "Это приватный бот таргетолога <b>Анастасии Жук</b> — "
    "специалиста по рекламе для инфобизнеса.\n\n"
    "🤖 Бот — её личная разработка и работает только для неё и её клиентов.\n\n"
    "🚀 Хотите запустить рекламу во <b>ВКонтакте</b>? "
    "Сначала напишите Анастасии — она проконсультирует и поможет с запуском:\n"
    f"👉 @{_CONTACT_USERNAME}"
)


def _contact_keyboard() -> InlineKeyboardMarkup:
    """Кнопка-переход в личку Анастасии."""
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="✍️ Написать Анастасии", url=_CONTACT_URL)]]
    )


@router.message()
async def greet_stranger(message: Message) -> None:
    """Любому не-оператору — визитка бота и приглашение написать Анастасии."""
    await message.answer(_MESSAGE, parse_mode="HTML", reply_markup=_contact_keyboard())
