"""Внутренний API кампаний (`/api/v1/campaigns`) — операторские операции.

Тонкий роутер: вся логика (поиск по тенанту, выбор канала, вызов площадки,
смена статуса) — в `services/launch_service`. SQL здесь нет (CLAUDE.md §1.3).
Снаружи путь закрыт на ingress (`infra/Caddyfile`), как соседние операторские
ресурсы: бот ходит в ядро внутри compose-сети, минуя Caddy.
"""

from __future__ import annotations

from typing import Annotated

from db.session import get_session
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from services.launch_service import CampaignStopError, stop_campaign
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

DEFAULT_ACCOUNT_ID = 1


class CampaignStopOut(BaseModel):
    campaign_id: int
    status: str
    # `None` — кампании не было на площадке (заглушка/сбой при создании): статус
    # сменили у себя, но останавливать во внешней системе было нечего.
    external_id: str | None = None


@router.post("/{campaign_id}/stop")
async def stop(
    campaign_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CampaignStopOut:
    """Остановить кампанию на площадке и зафиксировать статус `stopped`."""
    try:
        campaign = await stop_campaign(session, DEFAULT_ACCOUNT_ID, campaign_id)
    except CampaignStopError as exc:
        raise HTTPException(status_code=502, detail="campaign_stop_failed") from exc
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign_not_found")
    await session.commit()
    return CampaignStopOut(
        campaign_id=campaign.id, status=campaign.status, external_id=campaign.external_id
    )
