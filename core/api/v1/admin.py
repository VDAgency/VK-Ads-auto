"""Веб-админка оператора: авторизация + защита эндпоинтов `require_admin`.

Вход выдаёт бот (magic-link, `services/admin_auth`): оператор открывает `admin.html?token=`
→ `POST /admin/authenticate` проверяет токен и выставляет session-cookie. Публичного
эндпоинта «выдать ссылку» нет (минтить может только процесс с секретом — бот). Данные
админки (клиенты/брифы/кампании) — в отдельных роутерах под `require_admin` (Фаза 8а W3).
"""

from __future__ import annotations

from typing import Annotated

from config.settings import get_settings
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel
from services.admin_auth import (
    SESSION_TTL_SECONDS,
    generate_admin_session,
    verify_admin_link,
    verify_admin_session,
)

from core.api.rate_limit import cabinet_auth_rate_limit

router = APIRouter(prefix="/admin", tags=["admin"])

_SESSION_COOKIE = "admin_session"


class AdminAuthIn(BaseModel):
    token: str


class OkResponse(BaseModel):
    ok: bool = True


class AdminMe(BaseModel):
    operator_id: int


def _set_admin_cookie(response: Response, operator_id: int) -> None:
    settings = get_settings()
    token = generate_admin_session(operator_id, settings.secret_key.get_secret_value())
    response.set_cookie(
        _SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="lax",
        path="/",
    )


def require_admin(
    admin_session: Annotated[str | None, Cookie(alias=_SESSION_COOKIE)] = None,
) -> int:
    """FastAPI-зависимость: вернуть operator_id из admin-сессии или 401."""
    if admin_session:
        operator_id = verify_admin_session(
            admin_session, get_settings().secret_key.get_secret_value()
        )
        if operator_id is not None:
            return operator_id
    raise HTTPException(status_code=401, detail="admin_auth_required")


@router.post("/authenticate", dependencies=[Depends(cabinet_auth_rate_limit)])
async def authenticate(data: AdminAuthIn, response: Response) -> OkResponse:
    """Обменять admin magic-link токен (из бота) на session-cookie."""
    operator_id = verify_admin_link(data.token, get_settings().secret_key.get_secret_value())
    if operator_id is None:
        raise HTTPException(status_code=401, detail="Ссылка недействительна или истекла")
    _set_admin_cookie(response, operator_id)
    return OkResponse()


@router.post("/logout")
async def logout(response: Response) -> OkResponse:
    """Выход из админки — очистить session-cookie."""
    response.delete_cookie(_SESSION_COOKIE, path="/")
    return OkResponse()


@router.get("/me")
async def me(operator_id: Annotated[int, Depends(require_admin)]) -> AdminMe:
    """Проверка сессии: вернуть operator_id (для дашборда)."""
    return AdminMe(operator_id=operator_id)
