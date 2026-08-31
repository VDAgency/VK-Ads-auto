"""Загрузка креатива под бриф: медиа → описание → отправка (триггер запуска РК).

Тонкий хендлер: бот принимает фото/видео и описание, скачивает медиа из Telegram,
кодирует base64 и шлёт в ядро (`POST /briefs/{id}/creative`) — вся раскладка/запуск
на стороне ядра (spec 2026-07-17). Боевой запуск VK заблокирован агентским статусом —
ядро возвращает статус `prepared` и честный текст.
"""

from __future__ import annotations

import base64
from html import escape as _escape
from typing import Any

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from config.settings import get_settings
from services.brief_parser import parse_budget
from services.launch import daily_budget_rub_from_amount

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import (
    AdAccountItem,
    AgencyCabinetRejected,
    BriefCard,
    BriefNotFound,
    CoreUnavailable,
    CreativeRejected,
)
from bot.keyboards import (
    ad_account_pick_keyboard,
    brief_card_keyboard,
    cabinet_create_confirm_keyboard,
    creative_confirm_keyboard,
    launch_goal_keyboard,
)
from bot.states import LaunchCampaign, UploadCreative

router = Router(name="creative")
router.message.filter(OperatorOnly())
router.callback_query.filter(OperatorOnly())

_UNAVAILABLE = "Сервис временно недоступен, попробуйте позже."
_NOT_FOUND = "Бриф не найден."
# Лимит бота на скачивание файла из Telegram (getFile) — 20 МБ.
_MAX_TG_BYTES = 20 * 1024 * 1024
_ASK_MEDIA = "🖼 Пришлите фото или видео для рекламы (одним сообщением)."
_ASK_DESCRIPTION = (
    "Добавьте описание: первая строка — заголовок (до 40 символов), остальное — текст "
    "(до 220). Или отправьте «-», чтобы без описания."
)
_TOO_BIG = "Файл больше 20 МБ — Telegram не даёт боту его скачать. Пришлите версию полегче."
_ASK_CABINET = "В каком рекламном кабинете запускаем кампанию?"
_NO_CABINETS = (
    "⚠️ Ни одного рекламного кабинета не добавлено — запускать некуда.\n"
    "Добавьте кабинет командой /cabinets и повторите."
)
_NO_LIVE_CABINETS = (
    "⚠️ Ни один кабинет сейчас не годится: VK не принимает их токены.\n"
    "Откройте /cabinets, проверьте кабинеты и обновите токен."
)

# Цели рекламы: (код, подпись, реализована ли). Нереализованные показываем
# «серыми» — оператор видит план, но выбрать не может: кнопка ведёт на «goal:soon»,
# а не на реальный код цели. «Сообщения» прошли боевой зонд 2026-08-23
# (integrations.vk_surfaces.VK_MESSAGES.verified=True) и включены. «Заявка через
# Senler» технически работает тем же пакетом VK, что и «Сообщения»
# (integrations.vk_surfaces.VK_SENLER) и включена в services/launch_service.
# SUPPORTED_GOALS — собственный боевой прогон под именем Senler тоже проведён
# 2026-08-24 (Surface.verified=True): площадка больше не «скоро» ни здесь, ни в
# каталоге площадок подписки.
GOALS: list[tuple[str, str, bool]] = [
    ("subscribers", "👥 Подписчики", True),
    ("messages", "✉️ Сообщения в сообщество", True),
    ("lead_form", "📝 Заявки — лид-форма", True),
    ("senler", "🤖 Заявка через Senler", True),
]

# Те же цели без эмодзи и без разметки кнопок — для карточки подтверждения запуска
# (Т3, spec 2026-08-25-cabinet-client-binding-design §2: «цель по-русски»). Считаем
# из GOALS, а не дублируем текстом, чтобы подписи не могли разойтись.
GOAL_LABELS: dict[str, str] = {code: label.split(" ", 1)[1] for code, label, _ in GOALS}


# --- C1: предложение завести клиенту кабинет автоматически ---------------------
#
# Шаг общий для обоих сценариев запуска (с креативом — `start_creative` ниже, без
# него — `bot/handlers/brief_card.py:launch_without_creative`), поэтому живёт здесь:
# `brief_card.py` уже зависит от этого модуля (`GOAL_LABELS`, `render_launch_confirmation`).

_CABINET_CREATE_HEADER = "🏢 <b>У клиента ещё нет своего рекламного кабинета</b>"
_CABINET_CREATE_EXPLAIN = (
    "Заведём его в вашем агентстве VK Рекламы — и реклама клиента будет идти "
    "именно с него, отдельно от общих кабинетов."
)
_CABINET_CREATE_HINT = "Можно создать кабинет сейчас либо выбрать кабинет вручную, как раньше."
# ИНН отсутствует — создание не предлагаем вовсе (прямое требование ревью), но это
# лишь предупреждение: поток продолжается обычным выбором кабинета. Строгий отказ
# здесь заблокировал бы вообще все запуски, пока агентский статус VK не подтверждён
# (общие кабинеты — единственный работающий путь сегодня, `vk_agency_confirmed`
# по умолчанию выключен, docs/superpowers/plans/2026-08-25-agency-cabinets.md).
# Само предупреждение показывается только когда `vk_agency_confirmed` включён —
# `offer_cabinet_creation` ниже выходит раньше, чем добраться сюда, иначе оно
# сыпалось бы на каждом запуске (ревью ветки: сегодня почти все кабинеты общие).
_NO_TAX_ID_FOR_CABINET = (
    "ℹ️ У клиента не указан ИНН, поэтому отдельный кабинет пока не завести — так "
    "требует закон о рекламе. Дособерите ИНН правкой брифа, тогда кабинет можно "
    "будет создать автоматически. Пока продолжаем с общим кабинетом, если он есть."
)
_NO_NAME_FOR_CABINET = (
    "ℹ️ У клиента не указано имя или название — автоматически создать кабинет не "
    "получится. Дособерите данные правкой брифа. Пока продолжаем с общим "
    "кабинетом, если он есть."
)
_CABINET_CREATED = "✅ Кабинет создан и подключён."
_CABINET_ALREADY_EXISTS = (
    "ℹ️ У клиента уже есть свой кабинет — используем его, повторно в VK не идём."
)


def _own_cabinet_missing(accounts: list[AdAccountItem], client_id: int | None) -> bool:
    """Нет ли у клиента СВОЕГО кабинета среди уже показанных `accounts`.

    `accounts` уже отфильтрован по клиенту (`list_ad_accounts(client_id=...)`):
    общие плюс закреплённые за ним. «Свой» — закреплённый именно за этим
    `client_id`, не общий. `client_id=None` — у брифа нет привязанного клиента
    вовсе (не должно случаться в норме, см. `BriefCard.client_id`) — заводить
    кабинет тогда решительно не для кого, шаг просто пропускаем.
    """
    if client_id is None:
        return False
    return not any(item.client_id == client_id for item in accounts)


def _cabinet_prereq_issue(card: BriefCard) -> str | None:
    """Чего не хватает, чтобы предложить автосоздание кабинета. `None` — всё есть."""
    if not (card.client_name or "").strip():
        return _NO_NAME_FOR_CABINET
    if not _tax_id(card):
        return _NO_TAX_ID_FOR_CABINET
    return None


def _variant_label(variant: str) -> str:
    """Тип лица рекламодателя для карточки создания кабинета — тот же корень
    формулировки, что `_VARIANT_RU` в `bot/handlers/brief_card.py:_render_card`."""
    return "физлицо" if variant == "individual" else "сообщество"


def render_cabinet_create_card(card: BriefCard) -> str:
    """Карточка предложения завести клиенту кабинет автоматически (C1): кто
    рекламодатель, какое имя получит кабинет, зачем это вообще делается.

    Экранируем то же самое, что и `render_launch_confirmation` — данные клиента
    приходят из брифа и могут содержать `<`/`&`. Имя кабинета показываем без
    ниши: бот её не вычисляет (см. `services/agency_cabinets.py::_cabinet_name` —
    автоопределение ниши по категории сообщества намеренно не реализовано),
    поэтому превью честно совпадает с тем, что реально уйдёт в VK.
    """
    full_name = _escape(card.client_name or "не указано")
    tax_id = _escape(_tax_id(card) or "не указан")
    variant_label = _variant_label(card.variant)
    lines = [
        _CABINET_CREATE_HEADER,
        "",
        f"👤 Рекламодатель: {full_name} ({variant_label})",
        f"🧾 ИНН: {tax_id}",
        f"🏷 Кабинет назовём: «{full_name}»",
        "",
        _CABINET_CREATE_EXPLAIN,
        "",
        _CABINET_CREATE_HINT,
    ]
    return "\n".join(lines)


async def offer_cabinet_creation(
    message: Message, card: BriefCard, accounts: list[AdAccountItem], *, action: str
) -> bool:
    """Показать шаг C1, если он нужен, перед выбором кабинета.

    `True` — показана карточка создания с кнопками, вызывающая сторона должна
    остановиться и ждать решение оператора (`cabcreate:*`/`cabcreate_skip:*`
    ниже). `False` — шаг не нужен (у клиента уже есть свой кабинет) либо ИНН/имя
    не хватает: тогда честно предупреждаем (`_cabinet_prereq_issue`), но поток
    продолжается обычным выбором кабинета — строгий отказ здесь заблокировал бы
    все запуски, пока агентский статус VK не подтверждён.

    Ранний выход, если `vk_agency_confirmed` выключен (CLAUDE.md §1.4): бот
    читает то же окружение, что и ядро (`config.settings.get_settings`), а
    операция всё равно откажет `AgencyDisabledError`, если до неё дойти. Без
    этого выхода шаг C1 показывался бы на каждом запуске — сегодня почти все
    кабинеты общие (привязка к клиентам появилась совсем недавно), значит
    практически весь трафик получал бы лишнюю карточку (или ложное
    предупреждение про недостающий ИНН) вместо прежнего прямого перехода к
    выбору кабинета (ревью ветки).
    """
    if not get_settings().vk_agency_confirmed:
        return False
    if not _own_cabinet_missing(accounts, card.client_id):
        return False
    issue = _cabinet_prereq_issue(card)
    if issue:
        await message.answer(issue)
        return False
    await message.answer(
        render_cabinet_create_card(card),
        parse_mode="HTML",
        reply_markup=cabinet_create_confirm_keyboard(card.brief_id, action),
    )
    return True


async def create_cabinet_or_report(
    message: Message, brief_id: int
) -> tuple[BriefCard, list[AdAccountItem]] | None:
    """Оператор нажал «Создать кабинет» — попросить ядро завести его и вернуть
    свежие бриф + список кабинетов. `None` — не получилось, причина уже
    показана оператору; вызывающая сторона ничего больше не делает (карточка
    предложения остаётся в чате с кнопкой «Выбрать кабинет вручную» — повторное
    нажатие никуда не делось).

    Перед вызовом ядра список кабинетов клиента запрашивается заново и
    перепроверяется через `_own_cabinet_missing` (ревью ветки §4): клавиатура
    после успеха из чата не убирается, а сама операция — три последовательных
    запроса в VK, так что повторное нажатие вполне реально. Если кабинет у
    клиента уже появился (с прошлого нажатия, которое успело завершиться), в
    VK второй раз не идём — иначе завели бы второго клиента агентства с
    отдельным токеном; вместо этого честно сообщаем и продолжаем с уже
    существующим кабинетом, как будто он и был результатом этого нажатия.
    """
    try:
        card = await api_client.get_brief(brief_id)
    except BriefNotFound:
        await message.answer(_NOT_FOUND)
        return None
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        return None

    issue = _cabinet_prereq_issue(card)
    if card.client_id is None or issue:
        # Данные брифа изменились между показом карточки и нажатием кнопки
        # (например, ИНН стёрли правкой) — честно останавливаемся, а не идём
        # в ядро с заведомо отказным запросом.
        await message.answer(issue or _UNAVAILABLE)
        return None

    try:
        precheck_accounts = await api_client.list_ad_accounts(client_id=card.client_id)
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        return None
    if not _own_cabinet_missing(precheck_accounts, card.client_id):
        await message.answer(_CABINET_ALREADY_EXISTS)
        return card, precheck_accounts

    try:
        await api_client.create_agency_cabinet(
            card.client_id, card.client_name or "", _tax_id(card)
        )
    except AgencyCabinetRejected as exc:
        await message.answer(f"❌ {exc.reason}")
        return None
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        return None

    await message.answer(_CABINET_CREATED)
    try:
        fresh_card = await api_client.get_brief(brief_id)
        accounts = await api_client.list_ad_accounts(client_id=fresh_card.client_id)
    except BriefNotFound:
        await message.answer(_NOT_FOUND)
        return None
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        return None
    return fresh_card, accounts


@router.callback_query(F.data.startswith("creative:"))
async def start_creative(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать запуск по кнопке карточки брифа: сперва кабинет, потом цель.

    Кабинет и цель спрашиваем ДО материалов (spec 2026-07-27 §9): если живого
    кабинета нет, оператор узнает об этом сразу, а не после выгрузки видео.

    Список кабинетов запрашивается для клиента ЭТОГО брифа (`client_id` брифа
    из его карточки), а не весь пул (spec 2026-08-25-cabinet-client-binding-design
    §Т3): оператору незачем видеть и тем более случайно выбрать кабинет, закреплённый
    за другим клиентом.
    """
    brief_id = int((callback.data or "").split(":", 1)[1])
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    message = callback.message

    try:
        card = await api_client.get_brief(brief_id)
    except BriefNotFound:
        await message.answer(_NOT_FOUND)
        await callback.answer()
        return
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        await callback.answer()
        return

    try:
        accounts = await api_client.list_ad_accounts(client_id=card.client_id)
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        await callback.answer()
        return

    if await offer_cabinet_creation(message, card, accounts, action="creative"):
        # Карточка создания кабинета показана (C1) — ждём решение оператора
        # (`cabcreate:creative:*`/`cabcreate_skip:creative:*` ниже).
        await callback.answer()
        return

    await _continue_cabinet_choice(message, state, brief_id, card, accounts)
    await callback.answer()


async def _continue_cabinet_choice(
    message: Message,
    state: FSMContext,
    brief_id: int,
    card: BriefCard,
    accounts: list[AdAccountItem],
) -> None:
    """Хвост выбора кабинета: общий для первого захода `start_creative` и для
    обоих исходов шага C1 (кабинет создан либо оператор выбрал вручную)."""
    usable = [item for item in accounts if item.is_usable]
    if not accounts:
        await message.answer(_NO_CABINETS)
        return
    if not usable:
        await message.answer(_NO_LIVE_CABINETS)
        return

    await state.set_state(LaunchCampaign.choosing_cabinet)
    # `client_id` брифа остаётся в FSM, чтобы `picked_cabinet` ниже мог им
    # воспользоваться при повторном запросе списка — тот же принцип фильтрации,
    # что уже применён в этом самом вызове (ревью операторского опыта §2.5).
    await state.update_data(brief_id=brief_id, client_id=card.client_id)
    if len(usable) == 1:
        # Один кабинет — выбирать не из чего, но подтверждение показываем:
        # оператор должен видеть, куда именно уедет кампания.
        await _ask_goal(message, state, brief_id, usable[0])
    else:
        await message.answer(
            _ASK_CABINET,
            reply_markup=ad_account_pick_keyboard(
                [(item.id, f"{item.title} (id {item.external_id})") for item in usable],
                f"launch:{brief_id}",
            ),
        )


@router.callback_query(F.data.startswith("cabcreate:creative:"))
async def confirm_cabinet_create_for_creative(callback: CallbackQuery, state: FSMContext) -> None:
    """Оператор подтвердил создание кабинета в сценарии с креативом (C1)."""
    brief_id = int((callback.data or "").rsplit(":", 1)[1])
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    message = callback.message

    result = await create_cabinet_or_report(message, brief_id)
    if result is not None:
        card, accounts = result
        await _continue_cabinet_choice(message, state, brief_id, card, accounts)
    await callback.answer()


@router.callback_query(F.data.startswith("cabcreate_skip:creative:"))
async def skip_cabinet_create_for_creative(callback: CallbackQuery, state: FSMContext) -> None:
    """Оператор отказался от автосоздания — выбираем кабинет вручную, как раньше."""
    brief_id = int((callback.data or "").rsplit(":", 1)[1])
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    message = callback.message

    try:
        card = await api_client.get_brief(brief_id)
    except BriefNotFound:
        await message.answer(_NOT_FOUND)
        await callback.answer()
        return
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        await callback.answer()
        return
    try:
        accounts = await api_client.list_ad_accounts(client_id=card.client_id)
    except CoreUnavailable:
        await message.answer(_UNAVAILABLE)
        await callback.answer()
        return

    await _continue_cabinet_choice(message, state, brief_id, card, accounts)
    await callback.answer()


async def _ask_goal(
    message: Message, state: FSMContext, brief_id: int, account: AdAccountItem
) -> None:
    """Запомнить кабинет и предложить выбрать цель рекламы.

    Запоминаем не только id, но и поля кабинета, нужные карточке подтверждения
    (`render_launch_confirmation`) на шаге `got_description` — там кабинет заново
    не запрашивается, чтобы не показывать оператору данные, которые могли смениться
    прямо во время загрузки материалов.

    `account.title` — заголовок рекламного кабинета из VK, он может содержать `<`/`&`.
    Сообщение отправляется с `parse_mode="HTML"`, поэтому заголовок обязан пройти
    через `html.escape` — иначе Telegram отклоняет весь `sendMessage`, а aiogram
    эту ошибку молча глотает: оператор не получает вообще никакого ответа. Тот же
    дефект и то же лечение, что в `bot/handlers/surfaces.py`.
    """
    await state.update_data(
        ad_account_id=account.id,
        ad_account_title=account.title,
        ad_account_external_id=account.external_id,
        ad_account_advertiser_kind=account.advertiser_kind,
        ad_account_advertiser_name=account.advertiser_name,
        ad_account_advertiser_inn=account.advertiser_inn,
        ad_account_client_id=account.client_id,
        ad_account_client_name=account.client_name,
    )
    await state.set_state(LaunchCampaign.choosing_goal)
    await message.answer(
        f"Кабинет: <b>{_escape(account.title)}</b>\n\nВыберите цель рекламы:",
        parse_mode="HTML",
        reply_markup=launch_goal_keyboard(brief_id, GOALS),
    )


def _fallback_account(ad_account_id: int) -> AdAccountItem:
    """Кабинет пропал из списка между показом клавиатуры и нажатием (редкая гонка) —
    минимальная заглушка, лишь бы карточка не падала. Настоящая сверка — на ядре."""
    return AdAccountItem(
        id=ad_account_id,
        title="выбранный кабинет",
        external_id="",
        username=None,
        token_tail="",
        advertiser_kind="owner",
        advertiser_name=None,
        advertiser_inn=None,
        status="active",
        health="unknown",
        health_checked_at=None,
        health_error=None,
        balance_rub=None,
        is_usable=True,
    )


@router.callback_query(
    F.data.startswith("adacc:launch:"), StateFilter(LaunchCampaign.choosing_cabinet)
)
async def picked_cabinet(callback: CallbackQuery, state: FSMContext) -> None:
    """Оператор выбрал кабинет — переходим к цели.

    Список перезапрашивается для клиента ЭТОГО брифа (`client_id`, сохранённый
    в FSM в `start_creative`), а не всего пула (ревью операторского опыта §2.5,
    тот же дефект и то же выравнивание, что в
    `bot/handlers/brief_card.py:picked_cabinet_for_launch`).
    """
    parts = (callback.data or "").split(":")
    brief_id, ad_account_id = int(parts[2]), int(parts[3])
    if isinstance(callback.message, Message):
        data = await state.get_data()
        try:
            accounts = await api_client.list_ad_accounts(client_id=data.get("client_id"))
        except CoreUnavailable:
            await callback.message.answer(_UNAVAILABLE)
            await callback.answer()
            return
        account = next(
            (a for a in accounts if a.id == ad_account_id), _fallback_account(ad_account_id)
        )
        await _ask_goal(callback.message, state, brief_id, account)
    await callback.answer()


@router.callback_query(F.data == "goal:soon")
async def goal_not_ready(callback: CallbackQuery) -> None:
    """Цель ещё не реализована — говорим честно, вместо подмены на «подписчиков».

    Сейчас реализованы все четыре цели из `GOALS` — эта ветка недостижима из
    текущей клавиатуры, но остаётся на будущее: следующая ещё не готовая цель
    снова попадёт сюда, а не будет молча подменена.
    """
    await callback.answer(
        "Эта цель ещё не реализована. Сейчас доступны «Подписчики», "
        "«Сообщения в сообщество», «Заявки — лид-форма» и «Заявка через Senler».",
        show_alert=True,
    )


@router.callback_query(F.data.startswith("goal:"), StateFilter(LaunchCampaign.choosing_goal))
async def picked_goal(callback: CallbackQuery, state: FSMContext) -> None:
    """Цель выбрана — только теперь просим материалы."""
    parts = (callback.data or "").split(":")
    goal = parts[1]
    await state.update_data(goal=goal)
    await state.set_state(UploadCreative.waiting_media)
    if isinstance(callback.message, Message):
        await callback.message.answer(_ASK_MEDIA)
    await callback.answer()


@router.message(StateFilter(UploadCreative.waiting_media))
async def got_media(message: Message, state: FSMContext) -> None:
    """Принять фото/видео: запомнить файл и попросить описание."""
    if message.photo:
        photo = message.photo[-1]
        media_type, file_id = "photo", photo.file_id
        width, height, size = photo.width, photo.height, photo.file_size or 0
    elif message.video:
        video = message.video
        media_type, file_id = "video", video.file_id
        width, height, size = video.width, video.height, video.file_size or 0
    else:
        await message.answer(_ASK_MEDIA)
        return

    if size > _MAX_TG_BYTES:
        await message.answer(_TOO_BIG)
        return

    await state.update_data(file_id=file_id, media_type=media_type, width=width, height=height)
    await state.set_state(UploadCreative.waiting_description)
    await message.answer(_ASK_DESCRIPTION)


def _split_description(text: str) -> tuple[str, str]:
    """Первая строка → заголовок, остальное → текст. «-» → без описания."""
    if text.strip() == "-":
        return "", ""
    parts = text.split("\n", 1)
    title = parts[0].strip()
    body = parts[1].strip() if len(parts) > 1 else ""
    return title, body


def _field_value(card: BriefCard, *labels: str) -> str:
    """Значение первого поля брифа с одной из подписей (individual/community расходятся)."""
    for field in card.fields:
        if field.label in labels:
            return field.value
    return ""


def _tax_id(card: BriefCard) -> str:
    """ИНН клиента из брифа. Подпись поля разная у вариантов («ИНН» /
    «ИНН / ОГРН / ОГРНИП», services/brief_fields.py) — обе начинаются с «ИНН»."""
    for field in card.fields:
        if field.label.startswith("ИНН"):
            return field.value
    return ""


def _advertiser_line(account: AdAccountItem) -> str:
    """Конечный рекламодатель кабинета — та же логика, что в списке кабинетов
    (`bot/handlers/ad_accounts.py:_account_line`), продублирована здесь намеренно:
    импорт приватной функции другого хендлера создал бы скрытую связь между
    модулями разных задач владения."""
    if account.advertiser_kind == "third_party":
        name = account.advertiser_name or "не указан"
        inn = f", ИНН {account.advertiser_inn}" if account.advertiser_inn else ""
        return f"{name}{inn}"
    return "владелец кабинета (реклама от своего имени)"


def _binding_line(account: AdAccountItem) -> str:
    """Закреплён ли кабинет за клиентом — тот же корень формулировки, что в
    /cabinets (`bot/handlers/ad_accounts.py:_client_binding_label`: «кабинет
    общий — доступен любому клиенту»), для единого языка бота (ревью
    операторского опыта §2.4: раньше здесь звучало «не закреплён», а в
    /cabinets — «общий», хотя этот комментарий утверждал обратное). Здесь фраза
    не дословная — это самостоятельное предложение карточки подтверждения
    оплаты, а не продолжение строки списка, плюс совет проверить счёт."""
    if account.client_id is None:
        return "Кабинет общий — доступен любому клиенту. Проверьте, что запускаете с нужного счёта."
    if account.client_name:
        return f"Кабинет закреплён за этим клиентом: {_escape(account.client_name)}."
    return "Кабинет закреплён за этим клиентом (имя не указано)."


_BALANCE_WARNING = (
    "⚠️ Баланс меньше дневного бюджета из брифа — это не сбой, а повод пополнить "
    "кабинет. Пополнить может только сам агентский аккаунт в интерфейсе VK, не "
    "менеджер. На запуск это не влияет — решение за вами."
)


def _daily_budget_rub(card: BriefCard) -> float | None:
    """Дневной бюджет из брифа (C2) — формула ровно одна на весь проект:
    `services.launch.daily_budget_rub_from_amount` (ревью, «Важное» — раньше
    здесь жила своя копия формулы, и это значило, что изменившееся округление
    или знаменатель в `services.launch` тихо разошлись бы с предупреждением
    здесь). Бот лишь разбирает сырую строку бюджета брифа
    (`services.brief_parser.parse_budget`) — само деление на срок кампании
    считает `services.launch`. `None` — бюджет не указан или «обсудим» (тогда
    сравнивать не с чем, предупреждение не показываем)."""
    amount, needs_discussion = parse_budget(_field_value(card, "Бюджет"))
    return daily_budget_rub_from_amount(amount, needs_discussion)


def _balance_line(account: AdAccountItem, card: BriefCard) -> str | None:
    """Строка баланса кабинета для карточки подтверждения запуска (C2).

    Баланс неизвестен (VK не ответил, свежий кабинет) — строку не показываем,
    та же логика, что в /cabinets (`bot/handlers/ad_accounts.py:_account_line`).
    Предупреждаем, если баланса меньше дневного бюджета брифа, но НЕ блокируем
    запуск (план 2026-08-25, «Баланс» в таблице решений) — решение оператора.
    """
    if not account.balance_rub:
        return None
    try:
        balance = float(account.balance_rub)
    except ValueError:
        return None
    line = f"💳 Баланс кабинета: {_escape(account.balance_rub)} ₽"
    daily_budget = _daily_budget_rub(card)
    if daily_budget is not None and balance < daily_budget:
        line += "\n" + _BALANCE_WARNING
    return line


def render_launch_confirmation(card: BriefCard, account: AdAccountItem, goal_label: str) -> str:
    """Карточка подтверждения запуска — клиент, объект, цель, бюджет, кабинет,
    отметка соответствия (spec 2026-08-25-cabinet-client-binding-design §2).

    Одна и та же карточка для обоих сценариев запуска (с креативом и без) —
    вызывающая сторона добавляет только свой хвост. Ядро само отказывает при
    несовпадении ИНН/привязки (Т2) — карточка их не проверяет, только показывает,
    чтобы оператор мог сверить глазами до отправки (бот остаётся тонким).

    Экранируем каждое значение, пришедшее из брифа/VK/оператора: неэкранированный
    `<`/`&` в HTML-сообщении молча рушит всю отправку целиком (тот же дефект, что
    чинили для заголовка кабинета в `_ask_goal`).

    Показываем и распознанную площадку (`card.surface_title`, ревью операторского
    опыта §2.6) — она уже есть в карточке брифа (`bot/handlers/brief_card.py:
    _render_card`) рядом с этой же ссылкой на объект и заметно ускоряет сверку
    глазами: без неё оператор видит только сырую ссылку и не понимает, во что
    именно бот превратил формулировку клиента.

    Баланс кабинета — если он известен (C2, `_balance_line`): показываем и,
    если он меньше дневного бюджета брифа, спокойно предупреждаем, не блокируя
    запуск — решение остаётся за оператором.
    """
    client_name = _escape(card.client_name or "не указан")
    client_inn = _escape(_tax_id(card) or "не указан")
    object_url = _escape(
        _field_value(card, "Ссылка на страницу VK", "Ссылка на объект продвижения") or "не указана"
    )
    budget = _escape(_field_value(card, "Бюджет") or "не указан")
    term = _escape(_field_value(card, "Срок / период") or "не указан")
    advertiser = _escape(_advertiser_line(account))

    lines = [
        "📋 <b>Проверьте перед запуском</b>",
        "",
        f"👤 Клиент: {client_name} · ИНН {client_inn}",
        f"🔗 Объект рекламы: {object_url}",
    ]
    if card.surface_title:
        lines.append(f"🎯 Площадка: {_escape(card.surface_title)}")
    lines += [
        f"🎯 Цель: {_escape(goal_label)}",
        f"💰 Бюджет: {budget} · срок: {term}",
        "",
        f"💼 Кабинет: {_escape(account.title)} (id {_escape(account.external_id)})",
        f"Конечный рекламодатель кабинета: {advertiser}",
        _binding_line(account),
    ]
    balance_line = _balance_line(account, card)
    if balance_line:
        lines.append(balance_line)
    return "\n".join(lines)


def _account_from_state(data: dict[str, Any]) -> AdAccountItem:
    """Восстановить кабинет, выбранный оператором в `_ask_goal`, из данных FSM."""
    return AdAccountItem(
        id=int(data["ad_account_id"]),
        title=str(data.get("ad_account_title", "")),
        external_id=str(data.get("ad_account_external_id", "")),
        username=None,
        token_tail="",
        advertiser_kind=str(data.get("ad_account_advertiser_kind", "owner")),
        advertiser_name=data.get("ad_account_advertiser_name"),
        advertiser_inn=data.get("ad_account_advertiser_inn"),
        status="active",
        health="unknown",
        health_checked_at=None,
        health_error=None,
        balance_rub=None,
        is_usable=True,
        client_id=data.get("ad_account_client_id"),
        client_name=data.get("ad_account_client_name"),
    )


@router.message(StateFilter(UploadCreative.waiting_description))
async def got_description(message: Message, state: FSMContext) -> None:
    """Принять описание и показать карточку подтверждения запуска (Т3).

    Бриф запрашивается заново (а не берётся из FSM) — чтобы карточка показывала
    актуальные данные, даже если оператор успел их поправить, пока грузил медиа.
    """
    title, body = _split_description(message.text or "")
    await state.update_data(title=title, body=body)
    data = await state.get_data()
    brief_id = int(data["brief_id"])

    try:
        card = await api_client.get_brief(brief_id)
    except BriefNotFound:
        await state.clear()
        await message.answer(_NOT_FOUND)
        return
    except CoreUnavailable:
        # Не сбрасываем состояние: описание уже принято, оператор может просто
        # повторить его тем же сообщением, когда ядро отзовётся.
        await message.answer(_UNAVAILABLE)
        return

    account = _account_from_state(data)
    goal_code = str(data.get("goal", ""))
    goal_label = GOAL_LABELS.get(goal_code, goal_code)

    lines = [render_launch_confirmation(card, account, goal_label), "", "Креатив:"]
    if title:
        lines.append(f"Заголовок: {_escape(title)}")
    if body:
        lines.append(f"Текст: {_escape(body)}")
    if not title and not body:
        lines.append("Без описания.")
    lines.append("")
    lines.append("Отправка запустит подготовку рекламной кампании.")
    await message.answer(
        "\n".join(lines), parse_mode="HTML", reply_markup=creative_confirm_keyboard()
    )


@router.callback_query(F.data == "creative_cancel")
async def cancel_creative(callback: CallbackQuery, state: FSMContext) -> None:
    """Отменить загрузку креатива."""
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.answer("Отменено.")
    await callback.answer()


@router.callback_query(F.data == "creative_send")
async def send_creative(callback: CallbackQuery, state: FSMContext, bot: Bot) -> None:
    """Скачать медиа из Telegram, отправить в ядро → подготовка/запуск кампании."""
    data = await state.get_data()
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    message = callback.message

    buffer = await bot.download(data["file_id"])
    if buffer is None:
        await state.clear()
        await message.answer(_TOO_BIG)
        await callback.answer()
        return
    media_b64 = base64.b64encode(buffer.read()).decode("ascii")

    brief_id = int(data["brief_id"])
    try:
        result = await api_client.upload_creative(
            brief_id,
            media_b64,
            str(data["media_type"]),
            int(data["width"]),
            int(data["height"]),
            str(data.get("title", "")),
            str(data.get("body", "")),
            ad_account_id=data.get("ad_account_id"),
            goal=data.get("goal"),
        )
    except BriefNotFound:
        await state.clear()
        await message.answer(_NOT_FOUND)
    except CreativeRejected as exc:
        await state.clear()
        await message.answer(f"⚠️ {exc.reason}")
    except CoreUnavailable:
        await state.clear()
        await message.answer(_UNAVAILABLE)
    else:
        await state.clear()
        await message.answer(result.message, reply_markup=brief_card_keyboard(brief_id))
    await callback.answer()
