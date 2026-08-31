"""Операторская карточка брифа: просмотр всех полей и применение правок.

Headless-ядро: сборка карточки и применение правок — здесь, рендер — в боте. Бот ходит
сюда через `GET /api/v1/briefs/{id}` и `PATCH /api/v1/briefs/{id}` (см. api_client).

`has_creative` / `campaign_status` заполняются на этапе приёма креатива и запуска РК
(T1-PR3); пока брифа нет привязанного креатива/кампании — `False` / `None`.
"""

from __future__ import annotations

from dataclasses import dataclass

from db.models import Brief
from db.repositories import (
    get_brief,
    get_client,
    get_creative_for_brief,
    get_latest_campaign_for_brief,
)
from integrations.vk_surfaces import surface_for
from sqlalchemy.ext.asyncio import AsyncSession

from services.agency_cabinets import cabinet_step_state
from services.brief_fields import apply_edits, numbered
from services.brief_parser import parse_target_type
from services.goals import NO_CREATIVE_GOAL, launch_goal_title, target_title


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
    # Площадка подписки, как её понял разбор брифа. Клиент пишет её словами, площадок
    # шесть, и непонятое значение молча трактуется как сообщество — поэтому распознанный
    # вариант считаем здесь, один раз, и показываем и в боте, и в веб-кабинете.
    surface_title: str
    # Нужен ли креатив этой площадке. Продвижение готового поста обходится без него,
    # и интерфейсы не должны просить у оператора картинку, которая никуда не пойдёт.
    surface_needs_creative: bool
    # Название цели запуска без креатива (правило `services.goals.NO_CREATIVE_GOAL`:
    # продвижение готового поста/клипа/трека всегда идёт под целью «подписчики»).
    # Раньше это решал каждый канал сам локальной константой — теперь ядро отдаёт
    # готовое название, чтобы каналы бриф не разбирали (CLAUDE.md §1.3).
    launch_goal_title: str
    # Состояние шага C1 «завести клиенту кабинет автоматически» (план
    # 2026-08-25-agency-cabinets, волна C, `services.agency_cabinets.cabinet_step_state`) —
    # каналы больше не читают `vk_agency_confirmed` и не разбирают бриф сами
    # (CLAUDE.md §1.3), а показывают то, что уже решило ядро.
    cabinet_step_available: bool = False
    cabinet_step_own_cabinet_exists: bool = False
    cabinet_step_blocked_reason: str | None = None
    # Числовой `Client.id` брифа (spec 2026-08-25-cabinet-client-binding-design §Т3) —
    # бот использует его, чтобы запросить кабинеты, пригодные именно этому клиенту
    # (`GET /ad-accounts?client_id=`), а не весь пул. В хвосте с дефолтом `None`, тот
    # же приём, что `AdAccountView.client_id` в Т1: старые прямые конструкторы
    # `BriefCardView(...)` в тестах не ломаются.
    client_id: int | None = None


async def _build_view(session: AsyncSession, account_id: int, brief: Brief) -> BriefCardView:
    kind = parse_target_type(brief.payload.get("target_type", "")).value
    client = (
        await get_client(session, account_id, brief.client_id)
        if brief.client_id is not None
        else None
    )
    fields = [
        BriefFieldView(number=n, label=field.label, value=value)
        for n, field, value in numbered(brief.payload, brief.variant)
    ]
    creative = await get_creative_for_brief(session, account_id, brief.id)
    campaign = await get_latest_campaign_for_brief(session, account_id, brief.id)
    cabinet_step = await cabinet_step_state(session, account_id, brief.id)
    return BriefCardView(
        brief_id=brief.id,
        variant=brief.variant,
        status=brief.status,
        client_name=client.full_name if client else None,
        client_email=client.email if client else None,
        client_phone=client.phone if client else None,
        client_telegram=client.telegram if client else None,
        fields=fields,
        has_creative=creative is not None,
        campaign_status=campaign.status if campaign is not None else None,
        surface_title=target_title(kind),
        surface_needs_creative=surface_for(kind).needs_creative,
        launch_goal_title=launch_goal_title(NO_CREATIVE_GOAL),
        cabinet_step_available=cabinet_step.available,
        cabinet_step_own_cabinet_exists=cabinet_step.own_cabinet_exists,
        cabinet_step_blocked_reason=cabinet_step.blocked_reason,
        client_id=brief.client_id,
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
