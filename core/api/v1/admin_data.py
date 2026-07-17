"""Данные админ-панели оператора (`/api/v1/admin/*`, всё под `require_admin`).

Тонкие роутеры: зовут те же сервисы/репозитории, что и бот (`brief_view`,
`invite_tracking`, `creative_intake`, репозитории), и переиспользуют модели/хелперы
из `briefs.py`. Бизнес-логики/SQL здесь нет. Скоуп — единственный тенант.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from db.repositories import (
    count_briefs_by_client,
    count_campaigns,
    count_clients,
    get_client,
    list_campaigns,
    list_client_briefs,
    list_clients,
)
from db.session import get_session
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from services.brief_parser import BriefValidationError
from services.brief_view import apply_brief_edits, get_brief_card
from services.creative_intake import CreativeError, intake_creative
from services.invite_tracking import InviteView, list_pending, list_recent
from services.launch_service import BriefNotFoundError
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.v1.admin import require_admin
from core.api.v1.briefs import (
    BriefCardOut,
    BriefEditIn,
    BriefEditOut,
    CreativeIn,
    CreativeLaunchOut,
    creative_http_error,
    to_card_out,
)

# Все эндпоинты требуют admin-сессию.
router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

DEFAULT_ACCOUNT_ID = 1


class OverviewOut(BaseModel):
    clients: int
    pending: int
    recent: int
    campaigns: int


class ClientRow(BaseModel):
    id: int
    full_name: str | None
    email: str | None
    phone: str | None
    telegram: str | None
    brief_count: int


class ClientsOut(BaseModel):
    items: list[ClientRow]


class ClientBrief(BaseModel):
    id: int
    variant: str
    status: str


class ClientDetailOut(BaseModel):
    id: int
    full_name: str | None
    email: str | None
    phone: str | None
    telegram: str | None
    briefs: list[ClientBrief]


class AdminBriefItem(BaseModel):
    contact: str
    contact_name: str | None
    variant: str
    channel: str
    sent_at: datetime | None
    received_at: datetime | None
    waiting_days: int
    brief_id: int | None


class AdminBriefsOut(BaseModel):
    items: list[AdminBriefItem]


class CampaignRow(BaseModel):
    id: int
    brief_id: int
    client_id: int | None
    client_name: str | None
    status: str
    objective: str
    created_at: datetime


class CampaignsOut(BaseModel):
    items: list[CampaignRow]


def _brief_item(view: InviteView) -> AdminBriefItem:
    return AdminBriefItem(
        contact=view.contact,
        contact_name=view.contact_name,
        variant=view.variant,
        channel=view.channel,
        sent_at=view.sent_at,
        received_at=view.received_at,
        waiting_days=view.waiting_days,
        brief_id=view.brief_id,
    )


@router.get("/overview")
async def overview(session: Annotated[AsyncSession, Depends(get_session)]) -> OverviewOut:
    """Счётчики для дашборда: клиенты / ждём / пришли за неделю / кампании."""
    pending = await list_pending(session, DEFAULT_ACCOUNT_ID)
    recent = await list_recent(session, DEFAULT_ACCOUNT_ID)
    return OverviewOut(
        clients=await count_clients(session, DEFAULT_ACCOUNT_ID),
        pending=len(pending),
        recent=len(recent),
        campaigns=await count_campaigns(session, DEFAULT_ACCOUNT_ID),
    )


@router.get("/clients")
async def clients(session: Annotated[AsyncSession, Depends(get_session)]) -> ClientsOut:
    """Список клиентов оператора с числом их брифов."""
    rows = await list_clients(session, DEFAULT_ACCOUNT_ID)
    counts = await count_briefs_by_client(session, DEFAULT_ACCOUNT_ID)
    return ClientsOut(
        items=[
            ClientRow(
                id=c.id,
                full_name=c.full_name,
                email=c.email,
                phone=c.phone,
                telegram=c.telegram,
                brief_count=counts.get(c.id, 0),
            )
            for c in rows
        ]
    )


@router.get("/clients/{client_id}")
async def client_detail(
    client_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ClientDetailOut:
    """Клиент + его брифы (статусы)."""
    client = await get_client(session, DEFAULT_ACCOUNT_ID, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="client_not_found")
    briefs = await list_client_briefs(session, DEFAULT_ACCOUNT_ID, client_id)
    return ClientDetailOut(
        id=client.id,
        full_name=client.full_name,
        email=client.email,
        phone=client.phone,
        telegram=client.telegram,
        briefs=[ClientBrief(id=b.id, variant=b.variant, status=b.status) for b in briefs],
    )


@router.get("/briefs")
async def briefs(
    session: Annotated[AsyncSession, Depends(get_session)],
    status: Annotated[Literal["pending", "recent"], Query()] = "recent",
) -> AdminBriefsOut:
    """Трекинг брифов: кого ждём (`pending`) или кто прислал за неделю (`recent`)."""
    if status == "pending":
        views = await list_pending(session, DEFAULT_ACCOUNT_ID)
    else:
        views = await list_recent(session, DEFAULT_ACCOUNT_ID)
    return AdminBriefsOut(items=[_brief_item(v) for v in views])


@router.get("/briefs/{brief_id}")
async def brief_detail(
    brief_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BriefCardOut:
    """Карточка брифа (нумерованные поля + контакты + статусы)."""
    view = await get_brief_card(session, DEFAULT_ACCOUNT_ID, brief_id)
    if view is None:
        raise HTTPException(status_code=404, detail="brief_not_found")
    return to_card_out(view)


@router.patch("/briefs/{brief_id}")
async def edit_brief(
    brief_id: int,
    data: BriefEditIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> BriefEditOut:
    """Правки `номер → значение` к брифу."""
    view, unknown = await apply_brief_edits(session, DEFAULT_ACCOUNT_ID, brief_id, data.edits)
    if view is None:
        raise HTTPException(status_code=404, detail="brief_not_found")
    await session.commit()
    card = to_card_out(view)
    return BriefEditOut(**card.model_dump(), unknown=unknown)


@router.post("/briefs/{brief_id}/creative", status_code=201)
async def upload_creative(
    brief_id: int,
    data: CreativeIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CreativeLaunchOut:
    """Приём креатива (триггер запуска РК) из админки."""
    try:
        outcome = await intake_creative(
            session,
            DEFAULT_ACCOUNT_ID,
            brief_id,
            media_b64=data.media_b64,
            media_type=data.media_type,
            width=data.width,
            height=data.height,
            title=data.title,
            body=data.body,
        )
    except CreativeError as exc:
        raise creative_http_error(exc) from exc
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


@router.get("/campaigns")
async def campaigns(session: Annotated[AsyncSession, Depends(get_session)]) -> CampaignsOut:
    """Список кампаний (клиент, статус, цель, дата)."""
    rows = await list_campaigns(session, DEFAULT_ACCOUNT_ID)
    names = {c.id: c.full_name for c in await list_clients(session, DEFAULT_ACCOUNT_ID)}
    return CampaignsOut(
        items=[
            CampaignRow(
                id=c.id,
                brief_id=c.brief_id,
                client_id=c.client_id,
                client_name=names.get(c.client_id) if c.client_id is not None else None,
                status=c.status,
                objective=c.objective,
                created_at=c.created_at,
            )
            for c in rows
        ]
    )
