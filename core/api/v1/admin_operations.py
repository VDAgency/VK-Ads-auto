"""Разные операции веб-админки без отдельного домена (`/api/v1/admin/*`, `require_admin`).

Веб-зеркала операторских путей, которых у веба ещё не было: справочник площадок
размещения, запуск кампании без креатива и привязка кабинета к клиенту. Та же
логика, что у бота — переиспользуются существующие сервисы и хелперы-роутеры
операторских модулей (`briefs.py`, `ad_accounts.py`), своей бизнес-логики здесь
нет (CLAUDE.md §1.3).
"""

from __future__ import annotations

from typing import Annotated

from db.session import get_session
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from services.ad_accounts import (
    AccountNotFoundError,
    AmbiguousAdAccountError,
    NoAdAccountError,
    TokenUnavailableError,
)
from services.goals import goal_titles, launch_goals, subscription_targets
from services.launch_service import (
    BriefNotFoundError,
    LaunchPreview,
    UnsupportedGoalError,
    launch_preview,
)
from services.secret_box import NotConfiguredError
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.v1.ad_accounts import (
    AdAccountClientIn,
    AdAccountOut,
    set_ad_account_client_response,
)
from core.api.v1.admin import require_admin
from core.api.v1.briefs import CreativeLaunchOut, LaunchIn, launch_brief_response

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])

# Скоуп единственного тенанта — та же конвенция, что в briefs.py/ad_accounts.py.
_DEFAULT_ACCOUNT_ID = 1


class SurfaceOut(BaseModel):
    """Одна площадка подписки (зеркало карточки из бот-команды `/surfaces`)."""

    kind: str
    title: str
    hint: str
    available: bool
    goal: str
    goal_title: str
    needs_creative: bool


class SurfacesOut(BaseModel):
    items: list[SurfaceOut]


@router.get("/surfaces")
async def admin_get_surfaces() -> SurfacesOut:
    """Справочник площадок размещения — веб-зеркало команды `/surfaces` в боте.

    Данные — из `services.goals` (единственный источник правды для бота и веба),
    своей логики здесь нет.
    """
    titles = goal_titles()
    return SurfacesOut(
        items=[
            SurfaceOut(
                kind=target.kind,
                title=target.title,
                hint=target.hint,
                available=target.available,
                goal=target.goal,
                goal_title=titles.get(target.goal, target.goal),
                needs_creative=target.needs_creative,
            )
            for target in subscription_targets()
        ]
    )


class LaunchGoalOut(BaseModel):
    """Одна цель запуска кампании (зеркало карточки мастера запуска в боте)."""

    code: str
    title: str
    implemented: bool


class LaunchGoalsOut(BaseModel):
    items: list[LaunchGoalOut]


@router.get("/goals")
async def admin_get_goals() -> LaunchGoalsOut:
    """Цели запуска кампании — веб-зеркало списка, который показывает бот при выборе цели.

    Данные — из `services.goals.launch_goals()` (единственный источник правды для
    бота и веба), своей логики здесь нет. Нереализованную цель отдаём как есть
    (`implemented=False`): выбрать её каналу нельзя, но список остаётся честным.
    """
    return LaunchGoalsOut(
        items=[
            LaunchGoalOut(code=goal.code, title=goal.title, implemented=goal.implemented)
            for goal in launch_goals()
        ]
    )


@router.post("/briefs/{brief_id}/launch", status_code=201)
async def admin_launch_brief(
    brief_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    data: LaunchIn | None = None,
) -> CreativeLaunchOut:
    """Запустить кампанию без креатива из веб-админки (зеркало бот-эндпоинта)."""
    ad_account_id = data.ad_account_id if data is not None else None
    allow_relaunch = data.allow_relaunch if data is not None else False
    return await launch_brief_response(
        session, brief_id, ad_account_id, allow_relaunch=allow_relaunch
    )


class LaunchPreviewOut(BaseModel):
    """Карточка предпросмотра запуска — веб-зеркало карточки подтверждения,
    которую бот показывает перед запуском (`render_launch_confirmation`).
    Токен кабинета сюда не попадает — только название и внешний id.
    """

    client_name: str | None
    client_tax_id: str | None
    object_url: str
    surface_title: str
    goal_title: str
    budget_text: str
    term_text: str
    ad_account_id: int
    ad_account_title: str
    ad_account_external_id: str
    ad_account_client_id: int | None
    ad_account_client_name: str | None
    ad_account_balance_rub: str | None
    daily_budget_rub: float | None
    balance_below_daily_budget: bool
    client_mismatch: bool


def _to_launch_preview_out(preview: LaunchPreview) -> LaunchPreviewOut:
    return LaunchPreviewOut(
        client_name=preview.client_name,
        client_tax_id=preview.client_tax_id,
        object_url=preview.object_url,
        surface_title=preview.surface_title,
        goal_title=preview.goal_title,
        budget_text=preview.budget_text,
        term_text=preview.term_text,
        ad_account_id=preview.ad_account_id,
        ad_account_title=preview.ad_account_title,
        ad_account_external_id=preview.ad_account_external_id,
        ad_account_client_id=preview.ad_account_client_id,
        ad_account_client_name=preview.ad_account_client_name,
        ad_account_balance_rub=preview.ad_account_balance_rub,
        daily_budget_rub=preview.daily_budget_rub,
        balance_below_daily_budget=preview.balance_below_daily_budget,
        client_mismatch=preview.client_mismatch,
    )


@router.get("/briefs/{brief_id}/launch-preview")
async def admin_get_launch_preview(
    brief_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
    ad_account_id: Annotated[int | None, Query()] = None,
    goal: Annotated[str | None, Query()] = None,
) -> LaunchPreviewOut:
    """Карточка предпросмотра запуска без креатива — веб обязан показать её перед
    запуском, ровно как бот показывает карточку подтверждения
    (`bot/handlers/creative.py::render_launch_confirmation`) и ждёт явного
    подтверждения оператора, прежде чем тратить деньги клиента. Без этого шага
    веб был бы опаснее бота: кнопка «Отправить и запустить» стреляла бы сразу.

    Только чтение — кампанию не создаёт и не запускает, это отдельный явный
    вызов (`POST /admin/briefs/{id}/launch`).

    `ad_account_id` — кабинет, который выбрал оператор; не передан — берётся
    кабинет по умолчанию (единственный активный), тем же правилом, что и
    реальный запуск. `goal` — цель, которую оператор выбрал на отдельном шаге
    мастера запуска в сценарии с креативом (код из `GET /admin/goals`); не
    передан — сводка показывает цель запуска без креатива, как и раньше.
    Коды отказов зеркалят реальный запуск: 404 — брифа или указанного кабинета
    нет, 409 — кабинетов нет вовсе, их несколько (угадывать нельзя) либо токен
    кабинета сейчас недоступен, 422 — переданная цель неизвестна или ещё не
    реализована (`goal_not_supported`, тот же код, что и у запуска/приёма
    креатива).
    """
    try:
        preview = await launch_preview(
            session, _DEFAULT_ACCOUNT_ID, brief_id, ad_account_id=ad_account_id, goal=goal
        )
    except BriefNotFoundError as exc:
        raise HTTPException(status_code=404, detail="brief_not_found") from exc
    except UnsupportedGoalError as exc:
        raise HTTPException(status_code=422, detail="goal_not_supported") from exc
    except NoAdAccountError as exc:
        raise HTTPException(status_code=409, detail="no_ad_account") from exc
    except AmbiguousAdAccountError as exc:
        raise HTTPException(status_code=409, detail="ambiguous_ad_account") from exc
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail="ad_account_not_found") from exc
    except (TokenUnavailableError, NotConfiguredError) as exc:
        raise HTTPException(status_code=409, detail="ad_account_token_unavailable") from exc
    return _to_launch_preview_out(preview)


@router.patch("/ad-accounts/{ad_account_id}/client")
async def admin_patch_ad_account_client(
    ad_account_id: int,
    payload: AdAccountClientIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountOut:
    """Привязать/отвязать кабинет от клиента из веб-админки (`client_id=None` — снять)."""
    return await set_ad_account_client_response(session, ad_account_id, payload)
