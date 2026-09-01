"""Клиентский мини-кабинет: magic-link, установка пароля, вход email+паролем, просмотр.

Мини-отчёт (B1, ТЗ 4.2 / критерий приёмки Блока 2) сознательно БЕЗ расхода — не
светим маржу, клиент платит за услугу, а не за медиабюджет (PROJECT.md §4.2.2):
клиент видит свои брифы и их статус, а также свои кампании с показами, кликами,
результатами и CTR — расхода в отдаваемых типах нет вовсе. Тонкий роутер — логика
в сервисах/репозиториях (`services.client_report.build_client_report`).

Первый вход — по magic-link (`?token=`) → обязательная установка пароля. Возвратный
вход — email + пароль. Сессия — подписанный токен в HttpOnly-cookie (spec §5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from config.settings import get_settings
from db.repositories import (
    find_client_by_contacts,
    find_client_by_email,
    get_client,
    list_client_briefs,
    revoke_client_sessions,
    set_client_password,
)
from db.session import get_session
from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from services.auth_magiclink import generate_token, verify_token
from services.cabinet_email import send_login_link
from services.client_report import build_client_report
from services.password import hash_password, verify_password
from services.referral import generate_ref_code
from services.session_token import DEFAULT_TTL_SECONDS, generate_session, verify_session
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.rate_limit import cabinet_auth_rate_limit

router = APIRouter(prefix="/cabinet", tags=["cabinet"])

DEFAULT_ACCOUNT_ID = 1
_SESSION_COOKIE = "cabinet_session"
_MIN_PASSWORD_LEN = 8
_LOGIN_LINK_TTL = 24 * 3600  # ссылка входа/сброса — сутки


class LinkRequest(BaseModel):
    """Запрос ссылки для входа по контакту клиента."""

    email: str | None = None
    phone: str | None = None
    telegram: str | None = None


class LinkResponse(BaseModel):
    # Ответ одинаков независимо от наличия клиента — не раскрываем существование (C5).
    ok: bool = True


class SetPasswordRequest(BaseModel):
    token: str
    password: str


class LoginRequest(BaseModel):
    email: str
    password: str


class OkResponse(BaseModel):
    ok: bool = True


class BriefStatus(BaseModel):
    id: int
    variant: str
    status: str


class ClientCampaignItem(BaseModel):
    """Одна кампания клиента в мини-отчёте. Поля расхода здесь нет и не будет (B1)."""

    external_id: str
    goal: str
    status: str
    shows: float
    clicks: float
    results: float
    ctr: float


class ClientReportView(BaseModel):
    """Мини-отчёт клиента: его кампании с метриками, без расхода."""

    campaigns: list[ClientCampaignItem]


class CabinetView(BaseModel):
    client_id: int
    full_name: str | None
    email: str | None
    phone: str | None
    telegram: str | None
    password_set: bool
    briefs: list[BriefStatus]
    referral_url: str
    report: ClientReportView


def _identify(
    secret: str, session_cookie: str | None, token: str | None, *, valid_from: datetime | None
) -> int | None:
    """Определить клиента по session-cookie или magic-ссылке.

    `valid_from` прокидывается в обе проверки: отзыв доступа обязан гасить и сессию,
    и ссылку. Гасить только сессию — значит не отзывать ничего: утёкшая ссылка
    продолжила бы пускать в кабинет.
    """
    if session_cookie:
        client_id = verify_session(session_cookie, secret, valid_from=valid_from)
        if client_id is not None:
            return client_id
    if token:
        return verify_token(token, secret, valid_from=valid_from)
    return None


def _set_session_cookie(response: Response, client_id: int) -> None:
    """Выдать HttpOnly-cookie с подписанным session-токеном."""
    settings = get_settings()
    token = generate_session(client_id, settings.secret_key.get_secret_value())
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        max_age=DEFAULT_TTL_SECONDS,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        path="/",
    )


@router.post("/set-password", dependencies=[Depends(cabinet_auth_rate_limit)])
async def set_password(
    data: SetPasswordRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OkResponse:
    """Установить пароль по magic-link токену (первый вход / сброс) и войти в кабинет.

    Токен проверяется ПЕРВЫМ, без границы отзыва: запрос приходит по той самой
    ссылке, которую отзыв мог погасить, — подняв границу раньше проверки, мы бы
    отвергли собственный запрос (spec §6.3). После установки пароля границу отзыва
    поднимаем заново — прежние сессии/ссылки этого клиента гаснут, а cookie этого
    устройства выставляется уже после, так что клиент остаётся в кабинете.
    """
    if len(data.password) < _MIN_PASSWORD_LEN:
        raise HTTPException(status_code=422, detail="password_too_short")
    client_id = verify_token(data.token, get_settings().secret_key.get_secret_value())
    if client_id is None:
        raise HTTPException(status_code=401, detail="Ссылка недействительна или истекла")
    client = await set_client_password(
        session, DEFAULT_ACCOUNT_ID, client_id, hash_password(data.password)
    )
    if client is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")
    await revoke_client_sessions(session, DEFAULT_ACCOUNT_ID, client_id)
    await session.commit()
    _set_session_cookie(response, client_id)
    return OkResponse()


@router.post("/login", dependencies=[Depends(cabinet_auth_rate_limit)])
async def login(
    data: LoginRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OkResponse:
    """Возвратный вход по email + паролю → выдать session-cookie."""
    client = await find_client_by_email(session, DEFAULT_ACCOUNT_ID, data.email)
    # Одинаковый ответ при неверном email/пароле/непоставленном пароле — не раскрываем детали.
    if client is None or client.password_hash is None:
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    if not verify_password(data.password, client.password_hash):
        raise HTTPException(status_code=401, detail="Неверный email или пароль")
    _set_session_cookie(response, client.id)
    return OkResponse()


@router.post("/logout")
async def logout(response: Response) -> OkResponse:
    """Выход из кабинета — очистить session-cookie."""
    response.delete_cookie(_SESSION_COOKIE, path="/")
    return OkResponse()


@router.post("/request-link", dependencies=[Depends(cabinet_auth_rate_limit)])
async def request_link(
    data: LinkRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LinkResponse:
    """Прислать ссылку для входа на email. Ответ одинаков, есть клиент или нет (не раскрываем).

    Если клиент найден и у него есть email — отправляем письмо со ссылкой (support@).
    Ошибка отправки не влияет на ответ (best-effort), чтобы не раскрыть наличие клиента.
    """
    client = await find_client_by_contacts(
        session, DEFAULT_ACCOUNT_ID, data.email, data.phone, data.telegram
    )
    if client is not None and client.email:
        settings = get_settings()
        token = generate_token(
            client.id, settings.secret_key.get_secret_value(), ttl_seconds=_LOGIN_LINK_TTL
        )
        magic_link = f"{settings.public_base_url}/cabinet.html?token={token}"
        await send_login_link(client.email, magic_link)
    return LinkResponse()


@router.get("")
async def view_cabinet(
    session: Annotated[AsyncSession, Depends(get_session)],
    token: Annotated[str | None, Query()] = None,
    session_cookie: Annotated[str | None, Cookie(alias=_SESSION_COOKIE)] = None,
) -> CabinetView:
    """Показать кабинет: по session-cookie или по magic-link токену (`?token=`).

    Проверка подписи идёт дважды: первый раз без границы отзыва — только чтобы
    узнать, о каком клиенте речь, и прочитать его `sessions_valid_from`; второй —
    уже с границей, чтобы решить, пускать ли. Иначе границу неоткуда взять до того,
    как известен client_id.
    """
    secret = get_settings().secret_key.get_secret_value()
    client_id = _identify(secret, session_cookie, token, valid_from=None)
    if client_id is None:
        raise HTTPException(status_code=401, detail="Ссылка недействительна или истекла")

    client = await get_client(session, DEFAULT_ACCOUNT_ID, client_id)
    if client is None:
        raise HTTPException(status_code=404, detail="Клиент не найден")

    if client.sessions_valid_from is not None:
        confirmed = _identify(secret, session_cookie, token, valid_from=client.sessions_valid_from)
        if confirmed is None:
            raise HTTPException(status_code=401, detail="Доступ отозван")

    briefs = await list_client_briefs(session, DEFAULT_ACCOUNT_ID, client_id)
    # Скоуп строго по client_id из сессии/токена, никогда из параметра запроса —
    # иначе один клиент мог бы подставить чужой id и увидеть чужую статистику.
    report = await build_client_report(session, DEFAULT_ACCOUNT_ID, client_id)
    settings = get_settings()
    ref_code = generate_ref_code(client.id, settings.secret_key.get_secret_value())
    return CabinetView(
        client_id=client.id,
        full_name=client.full_name,
        email=client.email,
        phone=client.phone,
        telegram=client.telegram,
        password_set=client.password_hash is not None,
        briefs=[BriefStatus(id=b.id, variant=b.variant, status=b.status) for b in briefs],
        referral_url=f"{settings.public_base_url}/?ref={ref_code}",
        report=ClientReportView(
            campaigns=[
                ClientCampaignItem(
                    external_id=row.external_id,
                    goal=row.goal,
                    status=row.status,
                    shows=row.shows,
                    clicks=row.clicks,
                    results=row.results,
                    ctr=row.ctr,
                )
                for row in report.campaigns
            ]
        ),
    )
