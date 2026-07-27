"""Загрузка креатива под бриф: медиа → описание → отправка (триггер запуска РК).

Тонкий хендлер: бот принимает фото/видео и описание, скачивает медиа из Telegram,
кодирует base64 и шлёт в ядро (`POST /briefs/{id}/creative`) — вся раскладка/запуск
на стороне ядра (spec 2026-07-17). Боевой запуск VK заблокирован агентским статусом —
ядро возвращает статус `prepared` и честный текст.
"""

from __future__ import annotations

import base64

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot import api_client
from bot.access import OperatorOnly
from bot.api_client import BriefNotFound, CoreUnavailable, CreativeRejected
from bot.keyboards import (
    ad_account_pick_keyboard,
    brief_card_keyboard,
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
# «серыми» — оператор видит план, но выбрать не может, а ядро всё равно
# отклонит такую цель (services/launch_service.SUPPORTED_GOALS).
GOALS: list[tuple[str, str, bool]] = [
    ("subscribers", "👥 Подписчики", True),
    ("messages", "✉️ Сообщения в сообщество", False),
    ("lead_form", "📝 Заявки — лид-форма", False),
    ("senler", "🤖 Заявка через Senler", False),
]


@router.callback_query(F.data.startswith("creative:"))
async def start_creative(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать запуск по кнопке карточки брифа: сперва кабинет, потом цель.

    Кабинет и цель спрашиваем ДО материалов (spec 2026-07-27 §9): если живого
    кабинета нет, оператор узнает об этом сразу, а не после выгрузки видео.
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

    await state.set_state(LaunchCampaign.choosing_cabinet)
    await state.update_data(brief_id=brief_id)
    if len(usable) == 1:
        # Один кабинет — выбирать не из чего, но подтверждение показываем:
        # оператор должен видеть, куда именно уедет кампания.
        await _ask_goal(message, state, brief_id, usable[0].id, usable[0].title)
    else:
        await message.answer(
            _ASK_CABINET,
            reply_markup=ad_account_pick_keyboard(
                [(item.id, f"{item.title} (id {item.external_id})") for item in usable],
                f"launch:{brief_id}",
            ),
        )
    await callback.answer()


async def _ask_goal(
    message: Message, state: FSMContext, brief_id: int, ad_account_id: int, title: str
) -> None:
    """Запомнить кабинет и предложить выбрать цель рекламы."""
    await state.update_data(ad_account_id=ad_account_id)
    await state.set_state(LaunchCampaign.choosing_goal)
    await message.answer(
        f"Кабинет: <b>{title}</b>\n\nВыберите цель рекламы:",
        parse_mode="HTML",
        reply_markup=launch_goal_keyboard(brief_id, GOALS),
    )


@router.callback_query(
    F.data.startswith("adacc:launch:"), StateFilter(LaunchCampaign.choosing_cabinet)
)
async def picked_cabinet(callback: CallbackQuery, state: FSMContext) -> None:
    """Оператор выбрал кабинет — переходим к цели."""
    parts = (callback.data or "").split(":")
    brief_id, ad_account_id = int(parts[2]), int(parts[3])
    if isinstance(callback.message, Message):
        try:
            accounts = await api_client.list_ad_accounts()
        except CoreUnavailable:
            await callback.message.answer(_UNAVAILABLE)
            await callback.answer()
            return
        title = next((a.title for a in accounts if a.id == ad_account_id), "выбранный кабинет")
        await _ask_goal(callback.message, state, brief_id, ad_account_id, title)
    await callback.answer()


@router.callback_query(F.data == "goal:soon")
async def goal_not_ready(callback: CallbackQuery) -> None:
    """Цель ещё не реализована — говорим честно, вместо подмены на «подписчиков»."""
    await callback.answer(
        "Эта цель ещё не реализована. Сейчас доступны «Подписчики».", show_alert=True
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


@router.message(StateFilter(UploadCreative.waiting_description))
async def got_description(message: Message, state: FSMContext) -> None:
    """Принять описание и показать подтверждение отправки."""
    title, body = _split_description(message.text or "")
    await state.update_data(title=title, body=body)
    summary = ["Проверьте креатив перед отправкой:"]
    if title:
        summary.append(f"Заголовок: {title}")
    if body:
        summary.append(f"Текст: {body}")
    if not title and not body:
        summary.append("Без описания.")
    summary.append("\nОтправка запустит подготовку рекламной кампании.")
    await message.answer("\n".join(summary), reply_markup=creative_confirm_keyboard())


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
