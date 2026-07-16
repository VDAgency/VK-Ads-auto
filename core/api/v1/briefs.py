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
from services.briefs import InviteTokenError, intake_brief
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
    """Результат приёма брифа."""

    brief_id: int
    client_id: int
    status: str


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
    return BriefOut(brief_id=brief.id, client_id=brief.client_id, status=brief.status)
