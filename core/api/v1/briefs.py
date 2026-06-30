"""Внутренний API приёма брифа (`POST /api/v1/briefs`).

Тонкий роутер: валидирует вход, зовёт сервис, рендерит результат. SQL и бизнес-логики
здесь нет (headless-инвариант).
"""

from __future__ import annotations

from typing import Annotated, Literal

from db.session import get_session
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from services.brief_parser import BriefValidationError, BriefVariant
from services.briefs import intake_brief
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/briefs", tags=["briefs"])

# Единственный тенант на текущем этапе (швы мульти-тенанта заложены, см. PROJECT.md §6).
DEFAULT_ACCOUNT_ID = 1


class BriefIn(BaseModel):
    """Входящий бриф: вариант (физлицо/сообщество) + сырые поля формы."""

    variant: Literal["individual", "community"]
    payload: dict[str, str]


class BriefOut(BaseModel):
    """Результат приёма брифа."""

    brief_id: int
    client_id: int
    status: str


@router.post("", status_code=201)
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
        )
    except BriefValidationError as exc:
        raise HTTPException(status_code=422, detail={"missing": exc.missing}) from exc

    assert brief.client_id is not None  # сервис всегда привязывает клиента
    return BriefOut(brief_id=brief.id, client_id=brief.client_id, status=brief.status)
