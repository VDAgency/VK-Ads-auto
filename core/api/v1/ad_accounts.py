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
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from integrations.vk_api import (
    VkAgencyClientForbidden,
    VkAgencyClientNotFound,
    VkAgencyClientUnavailable,
    VkAgencyClientValidationError,
)
from integrations.vk_oauth import (
    VkOAuthInvalidCredentials,
    VkOAuthNotConfigured,
    VkOAuthRejected,
    VkOAuthUnavailable,
)
from pydantic import BaseModel, Field
from services.ad_accounts import (
    ADVERTISER_OWNER,
    AccountNotFoundError,
    AdAccountView,
    ClientNotFoundError,
    DuplicateAccountError,
    add_account,
    check_health,
    delete_account,
    list_accounts,
    list_accounts_for_client,
    set_account_client,
)
from services.agency_cabinets import (
    AgencyCabinetClientGoneError,
    AgencyCabinetDuplicateError,
    AgencyCabinetPersistError,
    AgencyDisabledError,
    AgencyMissingTaxIdError,
    AgencyTokenIssuanceFailedError,
    create_client_cabinet,
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
    client_id: int | None
    client_name: str | None
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
    client_id: int | None = None


class AdAccountClientIn(BaseModel):
    """Новая привязка кабинета к клиенту. `client_id=None` делает кабинет снова общим."""

    client_id: int | None = None


class AgencyCabinetIn(BaseModel):
    """Вход заведения клиенту кабинета через агентский API VK (B2/B3).

    Токена здесь нет и не может быть — его выпускает и сохраняет сама операция
    (`services.agency_cabinets.create_client_cabinet`). `full_name`/`tax_id` —
    уже подтверждённые оператором данные рекламодателя (карточка подтверждения,
    волна C плана), эндпоинт их не переизвлекает из брифа.
    """

    client_id: int
    full_name: str = Field(min_length=1, max_length=255)
    tax_id: str = Field(min_length=1, max_length=16)
    niche: str | None = Field(default=None, max_length=255)


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
        client_id=view.client_id,
        client_name=view.client_name,
        status=view.status,
        health=view.health,
        health_checked_at=view.health_checked_at,
        health_error=view.health_error,
        balance_rub=view.balance_rub,
        is_usable=view.is_usable,
    )


async def list_ad_accounts_response(
    session: AsyncSession, client_id: int | None = None
) -> AdAccountsOut:
    """Список кабинетов (устаревшие health-check освежаются по пути).

    `client_id` сужает список до кабинетов, пригодных этому клиенту (Т2/Т3):
    общие плюс закреплённые за ним.
    """
    if client_id is None:
        views = await list_accounts(session, DEFAULT_ACCOUNT_ID)
    else:
        views = await list_accounts_for_client(session, DEFAULT_ACCOUNT_ID, client_id)
    await session.commit()
    return AdAccountsOut(items=[to_out(v) for v in views])


async def create_ad_account_response(session: AsyncSession, payload: AdAccountIn) -> AdAccountOut:
    """Добавить кабинет: токен проверяется у VK ДО записи в базу.

    Коды ответов разведены по причинам, чтобы оператор понимал, что делать:
    400 — токен не годится, 409 — кабинет уже добавлен, 422 — указанного
    клиента нет у тенанта, 503 — VK недоступен (про токен ничего не известно,
    стоит повторить), 500 — не настроен ключ.
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
            client_id=payload.client_id,
        )
    except InvalidTokenError:
        raise HTTPException(status_code=400, detail="invalid_token") from None
    except DuplicateAccountError:
        raise HTTPException(status_code=409, detail="duplicate_account") from None
    except VkUnreachableError:
        raise HTTPException(status_code=503, detail="vk_unreachable") from None
    except NotConfiguredError:
        raise HTTPException(status_code=500, detail="encryption_key_missing") from None
    except ClientNotFoundError:
        raise HTTPException(status_code=422, detail="client_not_found") from None
    await session.commit()
    return to_out(view)


async def create_agency_cabinet_response(
    session: AsyncSession, payload: AgencyCabinetIn
) -> AdAccountOut:
    """Завести клиенту кабинет VK автоматически (B2/B3): создать клиента у
    агентства, выпустить токен без подтверждения клиента, сохранить `AdAccount`
    привязанным к клиенту. Коды ответов разведены по причинам отказа так же
    подробно, как у ручного добавления (`create_ad_account_response`), плюс
    агентские: предохранитель выключен, ИНН не указан, VK не подтвердил
    агентский статус, половинчатый провал на полпути — причём последний не
    один общий код: `cabinet_duplicate` (409, кабинет с таким внешним id VK
    уже есть) и `cabinet_client_gone` (422, клиент исчез между проверкой и
    сохранением) разведены отдельно от общего `cabinet_persist_failed` (502),
    у каждого своё действие оператора и оба несут `vk_client_id`/
    `vk_username` уже созданного в VK клиента, чтобы было за что зацепиться.

    Три кода ниже (`vk_oauth_invalid_credentials`/`vk_oauth_rejected`/
    `vk_oauth_unavailable`) закрывают ревью ветки §3: приходят из выпуска
    СОБСТВЕННОГО токена агентства (`_build_agency_adapter` внутри
    `create_client_cabinet`, шаг ДО создания клиента в VK) — до них раньше не
    доходил ни один блок, кроме `VkOAuthNotConfigured` (пустые учётные
    данные), и наружу уходила голая внутренняя ошибка. В VK на этом шаге ещё
    ничего не создано, полусостояния нет — `vk_client_id`/`vk_username` в
    ответе не несём, текст просто зовёт администратора.
    """
    try:
        view = await create_client_cabinet(
            session,
            DEFAULT_ACCOUNT_ID,
            payload.client_id,
            full_name=payload.full_name,
            tax_id=payload.tax_id,
            niche=payload.niche,
        )
    except AgencyDisabledError:
        raise HTTPException(status_code=403, detail="agency_disabled") from None
    except AgencyMissingTaxIdError:
        raise HTTPException(status_code=422, detail="tax_id_required") from None
    except ClientNotFoundError:
        raise HTTPException(status_code=422, detail="client_not_found") from None
    except NotConfiguredError:
        raise HTTPException(status_code=500, detail="encryption_key_missing") from None
    except VkOAuthNotConfigured:
        raise HTTPException(status_code=500, detail="vk_oauth_not_configured") from None
    except VkOAuthInvalidCredentials:
        raise HTTPException(status_code=500, detail="vk_oauth_invalid_credentials") from None
    except VkOAuthRejected:
        raise HTTPException(status_code=502, detail="vk_oauth_rejected") from None
    except VkOAuthUnavailable:
        raise HTTPException(status_code=503, detail="vk_oauth_unavailable") from None
    except VkAgencyClientForbidden:
        raise HTTPException(status_code=403, detail="vk_agency_not_confirmed") from None
    except VkAgencyClientValidationError:
        raise HTTPException(status_code=400, detail="vk_rejected_client_data") from None
    except VkAgencyClientNotFound:
        raise HTTPException(status_code=404, detail="vk_client_not_found") from None
    except VkAgencyClientUnavailable:
        raise HTTPException(status_code=503, detail="vk_unreachable") from None
    except AgencyTokenIssuanceFailedError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "token_issuance_failed",
                "vk_client_id": exc.vk_client_id,
                "vk_username": exc.vk_username,
            },
        ) from None
    except AgencyCabinetDuplicateError as exc:
        # Подкласс `AgencyCabinetPersistError` — обязан идти раньше базового
        # класса, иначе тот перехватит его первым. Отдельный код и текст:
        # оператору нужно искать уже существующий дубль кабинета, а не
        # разбираться с исчезнувшим клиентом.
        raise HTTPException(
            status_code=409,
            detail={
                "error": "cabinet_duplicate",
                "vk_client_id": exc.vk_client_id,
                "vk_username": exc.vk_username,
            },
        ) from None
    except AgencyCabinetClientGoneError as exc:
        # Тоже подкласс `AgencyCabinetPersistError`, тоже должен идти раньше
        # него. Клиент исчез на середине операции — другое действие
        # оператора, чем при дубле кабинета.
        raise HTTPException(
            status_code=422,
            detail={
                "error": "cabinet_client_gone",
                "vk_client_id": exc.vk_client_id,
                "vk_username": exc.vk_username,
            },
        ) from None
    except AgencyCabinetPersistError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "error": "cabinet_persist_failed",
                "vk_client_id": exc.vk_client_id,
                "vk_username": exc.vk_username,
            },
        ) from None
    await session.commit()
    return to_out(view)


async def set_ad_account_client_response(
    session: AsyncSession, ad_account_id: int, payload: AdAccountClientIn
) -> AdAccountOut:
    """Привязать кабинет к клиенту, переназначить или снять привязку (`client_id=None`)."""
    try:
        view = await set_account_client(
            session, DEFAULT_ACCOUNT_ID, ad_account_id, payload.client_id
        )
    except AccountNotFoundError:
        raise HTTPException(status_code=404, detail="not_found") from None
    except ClientNotFoundError:
        raise HTTPException(status_code=422, detail="client_not_found") from None
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
    client_id: Annotated[int | None, Query()] = None,
) -> AdAccountsOut:
    """Список рекламных кабинетов оператора; `client_id` сужает до пригодных клиенту."""
    return await list_ad_accounts_response(session, client_id)


@router.post("", status_code=201)
async def post_ad_account(
    payload: AdAccountIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountOut:
    """Добавить рекламный кабинет по токену."""
    return await create_ad_account_response(session, payload)


@router.post("/agency-cabinets", status_code=201)
async def post_agency_cabinet(
    payload: AgencyCabinetIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountOut:
    """Завести клиенту рекламный кабинет VK автоматически: агентский API VK
    создаёт клиента, выпускает токен без подтверждения клиента и сохраняет его
    как `AdAccount`, закреплённый за `client_id`. Требует включённого
    предохранителя `vk_agency_confirmed` и непустого `tax_id` — см.
    `services.agency_cabinets.create_client_cabinet`.
    """
    return await create_agency_cabinet_response(session, payload)


@router.post("/{ad_account_id}/check")
async def post_ad_account_check(
    ad_account_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountOut:
    """Проверить, жив ли токен кабинета, прямо сейчас."""
    return await check_ad_account_response(session, ad_account_id)


@router.patch("/{ad_account_id}/client")
async def patch_ad_account_client(
    ad_account_id: int,
    payload: AdAccountClientIn,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdAccountOut:
    """Изменить привязку кабинета к клиенту (пусто в теле — снова общий)."""
    return await set_ad_account_client_response(session, ad_account_id, payload)


@router.delete("/{ad_account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_ad_account(
    ad_account_id: int,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> Response:
    """Удалить рекламный кабинет."""
    await delete_ad_account_response(session, ad_account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
