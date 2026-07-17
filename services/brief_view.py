"""Операторская карточка брифа: просмотр всех полей и применение правок.

Headless-ядро: сборка карточки и применение правок — здесь, рендер — в боте. Бот ходит
сюда через `GET /api/v1/briefs/{id}` и `PATCH /api/v1/briefs/{id}` (см. api_client).

`has_creative` / `campaign_status` заполняются на этапе приёма креатива и запуска РК
(T1-PR3); пока брифа нет привязанного креатива/кампании — `False` / `None`.
"""

from __future__ import annotations

from dataclasses import dataclass

from db.models import Brief
from db.repositories import get_brief, get_client
from sqlalchemy.ext.asyncio import AsyncSession

from services.brief_fields import apply_edits, numbered


@dataclass(frozen=True, slots=True)
class BriefFieldView:
    """Одно нумерованное поле карточки."""

    number: int
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class BriefCardView:
    """Карточка брифа для оператора: поля + контакты клиента + статусы."""

    brief_id: int
    variant: str
    status: str
    client_name: str | None
    client_email: str | None
    client_phone: str | None
    client_telegram: str | None
    fields: list[BriefFieldView]
    has_creative: bool
    campaign_status: str | None


async def _build_view(session: AsyncSession, account_id: int, brief: Brief) -> BriefCardView:
    client = (
        await get_client(session, account_id, brief.client_id)
        if brief.client_id is not None
        else None
    )
    fields = [
        BriefFieldView(number=n, label=field.label, value=value)
        for n, field, value in numbered(brief.payload, brief.variant)
    ]
    return BriefCardView(
        brief_id=brief.id,
        variant=brief.variant,
        status=brief.status,
        client_name=client.full_name if client else None,
        client_email=client.email if client else None,
        client_phone=client.phone if client else None,
        client_telegram=client.telegram if client else None,
        fields=fields,
        has_creative=False,
        campaign_status=None,
    )


async def get_brief_card(
    session: AsyncSession, account_id: int, brief_id: int
) -> BriefCardView | None:
    """Собрать карточку брифа. `None` — брифа нет у тенанта."""
    brief = await get_brief(session, account_id, brief_id)
    if brief is None:
        return None
    return await _build_view(session, account_id, brief)


async def apply_brief_edits(
    session: AsyncSession, account_id: int, brief_id: int, edits: dict[int, str]
) -> tuple[BriefCardView | None, list[int]]:
    """Применить правки `{номер: значение}` к payload брифа, вернуть обновлённую карточку.

    Возвращает `(None, [])`, если брифа нет. Иначе — `(карточка, неизвестные_номера)`.
    Коммит — на вызывающем роутере.
    """
    brief = await get_brief(session, account_id, brief_id)
    if brief is None:
        return None, []
    new_payload, unknown = apply_edits(brief.payload, brief.variant, edits)
    brief.payload = new_payload  # реассайн нового dict → SQLAlchemy пометит поле dirty
    await session.flush()
    view = await _build_view(session, account_id, brief)
    return view, unknown
