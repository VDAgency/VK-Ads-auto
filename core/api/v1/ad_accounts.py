"""Внутренний API рекламных кабинетов (`/api/v1/ad-accounts`), spec 2026-07-27 §8.3.

Тонкий роутер: вся логика — в `services.ad_accounts`. Эндпоинты операторские,
поэтому снаружи закрыты через `infra/Caddyfile` (404), как `/invites` и
`/cabinets`; веб ходит по зеркалу под `require_admin` (`admin_ad_accounts.py`).

Ключевой инвариант: **токен не появляется ни в одном ответе.** Наружу уходит
только хвост из четырёх символов — этого хватает, чтобы отличить кабинеты.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from db.session import get_session
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from services.ad_accounts import (
    ADVERTISER_OWNER,
    AccountNotFoundError,
    AdAccountView,
    DuplicateAccountError,
    add_account,
    check_health,
    delete_account,
    list_accounts,
)
from services.secret_box import NotConfiguredError
from services.vk_identity import InvalidTokenError, VkUnreachableError
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/ad-accounts", tags=["ad-accounts"])

# Скоуп единственного тенанта — та же конвенция, что в briefs.py/cabinets.py.
DEFAULT_ACCOUNT_ID = 1


class AdAccountOut(BaseModel):
    """Кабинет для показа. Поля с токеном здесь нет намеренно."""

    id: int
    title: str
    external_id: str
    username: str | None
    token_tail: str
    advertiser_kind: str
    advertiser_name: str | None
    advertiser_inn: str | None
    status: str
    health: str
    health_checked_at: datetime | None
    health_error: str | None
    balance_rub: str | None
    is_usable: bool


class AdAccountsOut(BaseModel):
    items: list[AdAccountOut]


class AdAccountIn(BaseModel):
    """Вход добавления. `token` приходит только сюда и дальше не возвращается."""

    token: str = Field(min_length=8, max_length=512)
    title: str | None = Field(default=None, max_length=255)
    refresh_token: str | None = Field(default=None, max_length=512)
    advertiser_kind: str = ADVERTISER_OWNER
    advertiser_name: str | None = Field(default=None, max_length=255)
    advertiser_inn: str | None = Field(default=None, max_length=16)


def to_out(view: AdAccountView) -> AdAccountOut:
    """Представление сервиса → тело ответа (общее для этого роутера и админского)."""
    return AdAccountOut(
        id=view.id,
        title=view.title,
        external_id=view.external_id,
        username=view.username,
        token_tail=view.token_tail,
        advertiser_kind=view.advertiser_kind,
        advertiser_name=view.advertiser_name,
        advertiser_inn=view.advertiser_inn,
        status=view.status,
        health=view.health,
        health_checked_at=view.health_checked_at,
        health_error=view.health_error,
        balance_rub=view.balance_rub,
        is_usable=view.is_usable,
    )


async def list_ad_accounts_response(session: AsyncSession) -> AdAccountsOut:
    """Список кабинетов (устаревшие health-check освежаются по пути)."""
    views = await list_accounts(session, DEFAULT_ACCOUNT_ID)
    await session.commit()
    return AdAccountsOut(items=[to_out(v) for v in views])


async def create_ad_account_response(session: AsyncSession, payload: AdAccountIn) -> AdAccountOut:
    """Добавить кабинет: токен проверяется у VK ДО записи в базу.

    Коды ответов разведены по причинам, чтобы оператор понимал, что делать:
    400 — токен не годится, 409 — кабинет уже добавлен, 503 — VK недоступен
    (про токен ничего не известно, стоит повторить), 500 — не настроен ключ.
    """
    try:
        view = await add_account(
            session,
            DEFAULT_ACCOUNT_ID,
            payload.token,
            title=payload.title,
            refresh_token=payload.refresh_token,
            advertiser_kind=payload.advertiser_kind,
            advertiser_name=payload.advertiser_name,
            advertiser_inn=payload.advertiser_inn,
        )
    except InvalidTokenError:
        raise HTTPException(status_code=400, detail="invalid_token") from None
    except DuplicateAccountError:
        raise HTTPException(status_code=409, detail="duplicate_account") from None
    except VkUnreachableError:
        raise HTTPException(status_code=503, detail="vk_unreachable") from None
    except NotConfiguredError:
        raise HTTPException(status_code=500, detail="encryption_key_missing") from None
    await session.commit()
    return to_out(view)


async def check_ad_account_response(session: AsyncSession, ad_account_id: int) -> AdAccountOut:
    """Принудительный health-check (кеш игнорируется)."""
    try:
        view = await check_health(session, DEFAULT_ACCOUNT_ID, ad_account_id)
    except AccountNotFoundError:
        raise HTTPException(status_code=404, detail="not_found") from None
    await session.commit()
    return to_out(view)


async def delete_ad_account_response(session: AsyncSession, ad_account_id: int) -> None:
    """Удаление: кабинет уходит из выбора, оба секрета стираются безвозвратно."""
    try:
        await delete_account(session, DEFAULT_ACCOUNT_ID, ad_account_id)
    except AccountNotFoundError:
        raise HTTPException(status_code=404, detail="not_found") from None
    await session.commit()


@router.get("")
async def get_ad_accounts(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountsOut:
    """Список рекламных кабинетов оператора."""
    return await list_ad_accounts_response(session)


@router.post("", status_code=201)
async def post_ad_account(
    payload: AdAccountIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountOut:
    """Добавить рекламный кабинет по токену."""
    return await create_ad_account_response(session, payload)


@router.post("/{ad_account_id}/check")
async def post_ad_account_check(
    ad_account_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountOut:
    """Проверить, жив ли токен кабинета, прямо сейчас."""
    return await check_ad_account_response(session, ad_account_id)


@router.delete("/{ad_account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_ad_account(
    ad_account_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Удалить рекламный кабинет."""
    await delete_ad_account_response(session, ad_account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
