"""FSM-состояния бота."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class SendBrief(StatesGroup):
    """Сценарий «отправить бриф клиенту»."""

    choosing_variant = State()  # выбор: физлицо / ИП
    entering_contact = State()  # ввод контакта клиента
    # Telegram не принял — предлагаем письмо. Отправляем только по явному нажатию.
    offering_email = State()  # показана кнопка «Отправить на <email>»
    entering_fallback_email = State()  # email клиента неизвестен, просим ввести


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


class AddAdAccount(StatesGroup):
    """Сценарий «добавить рекламный кабинет» (spec 2026-07-27 §10).

    Токен приходит сообщением, которое удаляется сразу после приёма, — тот же
    приём, что для пароля в `/link_kotbot`.
    """

    choosing_kind = State()  # своя реклама / реклама третьего лица
    entering_advertiser = State()  # название и ИНН конечного рекламодателя
    choosing_client = State()  # за каким клиентом закрепить кабинет (или общий)
    entering_token = State()  # ввод токена (сообщение сразу удаляется)


class AddCommunityToken(StatesGroup):
    """Сценарий «привязать токен сообщества для проверки Senler» (B2, spec 2026-08-24 §7).

    Токен приходит сообщением, которое удаляется сразу после приёма — тот же
    приём, что у `AddAdAccount.entering_token`. Id сообщества оператор не
    вводит вовсе — `groups.getById` называет сообщество по одному лишь токену
    (`core/api/v1/senler.py`), поэтому у сценария всего один шаг.
    """

    entering_token = State()  # ввод токена сообщества (сообщение сразу удаляется)


class UnlinkCommunityToken(StatesGroup):
    """Сценарий «отвязать токен сообщества» (ревью 2026-08-24, дефект 3).

    Раньше `db.community_tokens.delete_community_token` существовал, но
    ниоткуда не вызывался — у оператора не было способа снять устаревшую или
    ошибочную привязку иначе как правкой базы руками. Адрес/id сообщества — не
    секрет (в отличие от `AddCommunityToken.entering_token`), поэтому сообщение
    не удаляется.
    """

    entering_reference = State()  # короткий адрес сообщества или его числовой id


class SetPassword(StatesGroup):
    """Сценарий «задать пароль входа в веб-кабинет» (`/set_password`).

    Пароль приходит сообщением, которое удаляется сразу после приёма — тот же
    приём, что у `AddAdAccount.entering_token`.
    """

    entering_password = State()  # ввод пароля (сообщение сразу удаляется)


class LaunchCampaign(StatesGroup):
    """Сценарий запуска: кабинет → цель → креатив (spec 2026-07-27 §9).

    Кабинет и цель спрашиваем ДО материалов: если живого кабинета нет, оператор
    узнает об этом сразу, а не после выгрузки видео.
    """

    choosing_cabinet = State()  # выбор рекламного кабинета
    choosing_goal = State()  # выбор цели рекламы
