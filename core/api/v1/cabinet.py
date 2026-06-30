"""Клиентский мини-кабинет: запрос magic-link и просмотр статуса по токену.

Мини-отчёт сознательно БЕЗ расхода (не светим маржу, PROJECT.md §4.2.2): клиент
видит свои брифы и их статус; метрики кампаний (без расхода) добавятся, когда
кампании привязаны. Тонкий роутер — логика в сервисах/репозиториях.
"""

from __future__ import annotations

from typing import Annotated

from config.settings import get_settings
from db.repositories import find_client_by_contacts, get_client, list_client_briefs
from db.session import get_session
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from services.auth_magiclink import generate_token, verify_token
from services.referral import generate_ref_code
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/cabinet", tags=["cabinet"])

DEFAULT_ACCOUNT_ID = 1


class LinkRequest(BaseModel):
    """Запрос magic-link по контакту клиента."""

    email: str | None = None
    phone: str | None = None
    telegram: str | None = None


class LinkResponse(BaseModel):
    magic_link: str


class BriefStatus(BaseModel):
    id: int
    variant: str
    status: str


class CabinetView(BaseModel):
    client_id: int
    full_name: str | None
    briefs: list[BriefStatus]
    referral_url: str


@router.post("/request-link", status_code=201)
async def request_link(
    data: LinkRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LinkResponse:
    """Сгенерировать magic-link для клиента, найденного по контакту."""
    client = await find_client_by_contacts(
        session, DEFAULT_ACCOUNT_ID, data.email, data.phone, data.telegram
    )
    if client is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    settings = get_settings()
    token = generate_token(client.id, settings.secret_key.get_secret_value())
    return LinkResponse(magic_link=f"{settings.public_base_url}/cabinet.html?token={token}")


@router.get("")
async def view_cabinet(
    token: Annotated[str, Query()],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CabinetView:
    """Показать кабинет клиента по magic-link токену."""
    client_id = verify_token(token, get_settings().secret_key.get_secret_value())
    if client_id is None:
        raise HTTPException(status_code=401, detail="Ссылка недействительна или истекла")
    client = await get_client(session, DEFAULT_ACCOUNT_ID, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    briefs = await list_client_briefs(session, DEFAULT_ACCOUNT_ID, client_id)
    settings = get_settings()
    ref_code = generate_ref_code(client.id, settings.secret_key.get_secret_value())
    return CabinetView(
        client_id=client.id,
        full_name=client.full_name,
        briefs=[BriefStatus(id=b.id, variant=b.variant, status=b.status) for b in briefs],
        referral_url=f"{settings.public_base_url}/?ref={ref_code}",
    )
