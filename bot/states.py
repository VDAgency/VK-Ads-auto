"""FSM-состояния бота."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class SendBrief(StatesGroup):
    """Сценарий «отправить бриф клиенту»."""

    choosing_variant = State()  # выбор: физлицо / ИП
    entering_contact = State()  # ввод контакта клиента


class LinkUserbot(StatesGroup):
    """Сценарий «подключить юзер-бота»: телефон → код → пароль 2FA (spec §9)."""

    entering_phone = State()  # ввод номера телефона Анастасии
    entering_code = State()  # ввод кода из Telegram
    entering_password = State()  # ввод пароля 2FA (если включён)


class LinkKotbot(StatesGroup):
    """Сценарий «подключить kotbot»: стратегия → логин → пароль → код (spec 2026-07-17 §5)."""

    choosing_strategy = State()  # выбор: почта+пароль / VK-аккаунт
    entering_login = State()  # ввод логина (почта или логин VK)
    entering_password = State()  # ввод пароля (сообщение сразу удаляется)
    entering_code = State()  # ввод кода подтверждения (сообщение сразу удаляется)


class EditBrief(StatesGroup):
    """Сценарий «внести правки в бриф»: правки формата `номер.значение` (PROJECT.md §4.1.6)."""

    entering_edits = State()  # ввод правок (много строк за раз)


class UploadCreative(StatesGroup):
    """Сценарий «загрузить креатив»: медиа → описание → отправка (триггер запуска РК)."""

    waiting_media = State()  # ждём фото/видео
    waiting_description = State()  # ждём заголовок + текст
