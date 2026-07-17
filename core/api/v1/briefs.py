"""Внутренний API приёма брифа (`POST /api/v1/briefs`).

Тонкий роутер: валидирует вход, зовёт сервис, рендерит результат. SQL и бизнес-логики
здесь нет (headless-инвариант).
"""

from __future__ import annotations

import base64
import binascii
from typing import Annotated, Literal

from config.settings import get_settings
from db.session import get_session
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from services.auth_magiclink import generate_token
from services.brief_parser import BriefValidationError, BriefVariant
from services.brief_view import BriefCardView, apply_brief_edits, get_brief_card
from services.briefs import InviteTokenError, intake_brief
from services.creative_store import save_creative
from services.creative_validate import (
    ImageCreative,
    is_valid,
    validate_image,
    validate_text,
    validate_video_size,
)
from services.launch_service import BriefNotFoundError, launch_from_creative
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.rate_limit import brief_rate_limit

router = APIRouter(prefix="/briefs", tags=["briefs"])

# Единственный тенант на текущем этапе (швы мульти-тенанта заложены, см. PROJECT.md §6).
DEFAULT_ACCOUNT_ID = 1


class BriefIn(BaseModel):
    """Входящий бриф: вариант (физлицо/сообщество) + сырые поля формы.

    `token` — из ссылки-инвайта (`?t=`), если бриф пришёл по нашему приглашению.
    `ref_code` — из реферальной ссылки (Блок 2).
    """

    variant: Literal["individual", "community"]
    payload: dict[str, str]
    ref_code: str | None = None
    token: str | None = None


class BriefOut(BaseModel):
    """Результат приёма брифа. `cabinet_url` — авто-переброс в кабинет (magic-link)."""

    brief_id: int
    client_id: int
    status: str
    cabinet_url: str


# Короткий TTL ссылки первого входа в кабинет (сутки) — не «неделя» дефолта magic-link.
_CABINET_LINK_TTL = 24 * 3600


class BriefClientOut(BaseModel):
    """Контакты клиента в карточке брифа (операторский просмотр)."""

    full_name: str | None
    email: str | None
    phone: str | None
    telegram: str | None


class BriefFieldOut(BaseModel):
    """Одно нумерованное поле карточки брифа."""

    n: int
    label: str
    value: str


class BriefCardOut(BaseModel):
    """Карточка брифа для оператора: нумерованные поля + клиент + статусы."""

    brief_id: int
    variant: str
    status: str
    client: BriefClientOut
    fields: list[BriefFieldOut]
    has_creative: bool
    campaign_status: str | None = None


class BriefEditIn(BaseModel):
    """Правки формата `номер → значение` (парсинг `номер.значение` — на стороне бота)."""

    edits: dict[int, str]


class BriefEditOut(BriefCardOut):
    """Обновлённая карточка + номера правок вне диапазона полей варианта."""

    unknown: list[int] = []


class CreativeIn(BaseModel):
    """Загрузка креатива под бриф: медиа (base64) + тип + метаданные + текст.

    `width`/`height` — для валидации изображения (у бота они из Telegram; для видео
    не используются). `media_b64` декодируется, размер берётся по факту байтов.
    """

    media_b64: str
    media_type: Literal["photo", "video"]
    width: int = 0
    height: int = 0
    title: str = ""
    body: str = ""


class CreativeLaunchOut(BaseModel):
    """Итог приёма креатива и подготовки/запуска кампании."""

    campaign_status: str  # prepared | launched
    campaign_id: int
    message: str


# Защитный предел размера тела (бот и так качает из Telegram файлы ≤20 МБ).
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def _to_card_out(view: BriefCardView) -> BriefCardOut:
    return BriefCardOut(
        brief_id=view.brief_id,
        variant=view.variant,
        status=view.status,
        client=BriefClientOut(
            full_name=view.client_name,
            email=view.client_email,
            phone=view.client_phone,
            telegram=view.client_telegram,
        ),
        fields=[BriefFieldOut(n=f.number, label=f.label, value=f.value) for f in view.fields],
        has_creative=view.has_creative,
        campaign_status=view.campaign_status,
    )


@router.post("", status_code=201, dependencies=[Depends(brief_rate_limit)])
async def submit_brief(
    data: BriefIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BriefOut:
    """Принять бриф с веб-формы и сохранить в БД."""
    try:
        brief = await intake_brief(
            session,
            DEFAULT_ACCOUNT_ID,
            BriefVariant(data.variant),
            data.payload,
            source="web",
            ref_code=data.ref_code,
            token=data.token,
        )
    except BriefValidationError as exc:
        raise HTTPException(status_code=422, detail={"missing": exc.missing}) from exc
    except InviteTokenError as exc:
        # not_found → 404 (нет такого инвайта); inactive → 409 (уже принят/заменён).
        status_code = 404 if exc.code == "not_found" else 409
        raise HTTPException(status_code=status_code, detail=f"invite_{exc.code}") from exc

    assert brief.client_id is not None  # сервис всегда привязывает клиента
    settings = get_settings()
    token = generate_token(
        brief.client_id, settings.secret_key.get_secret_value(), ttl_seconds=_CABINET_LINK_TTL
    )
    cabinet_url = f"{settings.public_base_url}/cabinet.html?token={token}"
    return BriefOut(
        brief_id=brief.id,
        client_id=brief.client_id,
        status=brief.status,
        cabinet_url=cabinet_url,
    )


@router.get("/{brief_id}")
async def get_brief_detail(
    brief_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BriefCardOut:
    """Операторская карточка брифа: все поля (нумерованные) + контакты клиента + статусы."""
    view = await get_brief_card(session, DEFAULT_ACCOUNT_ID, brief_id)
    if view is None:
        raise HTTPException(status_code=404, detail="brief_not_found")
    return _to_card_out(view)


@router.patch("/{brief_id}")
async def edit_brief(
    brief_id: int,
    data: BriefEditIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BriefEditOut:
    """Применить правки `номер → значение` к брифу и вернуть обновлённую карточку."""
    view, unknown = await apply_brief_edits(session, DEFAULT_ACCOUNT_ID, brief_id, data.edits)
    if view is None:
        raise HTTPException(status_code=404, detail="brief_not_found")
    await session.commit()
    card = _to_card_out(view)
    return BriefEditOut(**card.model_dump(), unknown=unknown)


@router.post("/{brief_id}/creative", status_code=201)
async def upload_creative(
    brief_id: int,
    data: CreativeIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CreativeLaunchOut:
    """Принять креатив (триггер запуска РК): валидировать, сохранить, создать кампанию."""
    try:
        raw = base64.b64decode(data.media_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="media_b64_invalid") from exc
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="media_too_large")

    if data.media_type == "photo":
        issues = validate_image(
            ImageCreative(fmt="jpg", width=data.width, height=data.height, size_bytes=len(raw))
        )
    else:
        issues = validate_video_size(len(raw))
    issues += validate_text(data.title, data.body)
    if not is_valid(issues):
        raise HTTPException(status_code=422, detail={"issues": issues})

    path = save_creative(brief_id, data.media_type, raw)
    try:
        outcome = await launch_from_creative(
            session,
            DEFAULT_ACCOUNT_ID,
            brief_id,
            data.media_type,
            path,
            data.title or None,
            data.body or None,
        )
    except BriefNotFoundError as exc:
        raise HTTPException(status_code=404, detail="brief_not_found") from exc
    except BriefValidationError as exc:
        raise HTTPException(status_code=422, detail={"missing": exc.missing}) from exc
    await session.commit()
    return CreativeLaunchOut(
        campaign_status=outcome.campaign_status,
        campaign_id=outcome.campaign_id,
        message=outcome.message,
    )
