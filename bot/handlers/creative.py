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
from bot.keyboards import brief_card_keyboard, creative_confirm_keyboard
from bot.states import UploadCreative

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


@router.callback_query(F.data.startswith("creative:"))
async def start_creative(callback: CallbackQuery, state: FSMContext) -> None:
    """Начать загрузку креатива по кнопке карточки брифа."""
    brief_id = int((callback.data or "").split(":", 1)[1])
    await state.set_state(UploadCreative.waiting_media)
    await state.update_data(brief_id=brief_id)
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
