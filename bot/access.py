"""Фильтр доступа: бот принимает команды только от операторов (по Telegram ID)."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message
from config.settings import get_settings


class OperatorOnly(BaseFilter):
    """Пропускает только сообщения/нажатия от операторов из конфига."""

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return user is not None and get_settings().is_operator(user.id)


class NonOperator(BaseFilter):
    """Пропускает всех, кто НЕ оператор — для визитки-приветствия чужим.

    Дополняет `OperatorOnly`: операторские роутеры разбирают свои апдейты первыми,
    не-операторские доходят до роутера-визитки (регистрируется последним).
    """

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        user = event.from_user
        return user is not None and not get_settings().is_operator(user.id)
