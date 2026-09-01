"""Веб-админка оператора: авторизация + защита эндпоинтов `require_admin`.

Вход выдаёт бот (magic-link, `services/admin_auth`): оператор открывает `admin.html?token=`
→ `POST /admin/authenticate` проверяет токен и выставляет session-cookie. Публичного
эндпоинта «выдать ссылку» нет (минтить может только процесс с секретом — бот). Данные
админки (клиенты/брифы/кампании) — в отдельных роутерах под `require_admin` (Фаза 8а W3).

С spec 2026-08-31 добавлен возвратный вход паролем (`POST /admin/login`) и смена пароля
(`POST /admin/password`, `services/operator_auth.py`) — по образцу клиентского кабинета
(`core/api/v1/cabinet.py`). Важно: identity здесь — Telegram ID оператора (то же, что несёт
admin-сессия), а не `Operator.id` из БД — не перепутать при обращении к сервису.
"""

from __future__ import annotations

from typing import Annotated

from config.settings import get_settings
from db.repositories import (
    get_operator_sessions_valid_from,
    get_or_create_operator,
    revoke_operator_sessions,
)
from db.session import get_session
from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel
from services.admin_auth import (
    SESSION_TTL_SECONDS,
    generate_admin_session,
    verify_admin_link,
    verify_admin_session,
)
from services.operator_auth import WeakPasswordError, authenticate_operator, set_operator_password
from sqlalchemy.ext.asyncio import AsyncSession

from core.api.rate_limit import cabinet_auth_rate_limit

router = APIRouter(prefix="/admin", tags=["admin"])

_SESSION_COOKIE = "admin_session"
DEFAULT_ACCOUNT_ID = 1


class AdminAuthIn(BaseModel):
    token: str


class AdminLoginIn(BaseModel):
    """Возвратный вход в админку: Telegram ID оператора + пароль."""

    telegram_id: int
    password: str


class AdminPasswordIn(BaseModel):
    """Установка/смена пароля оператора текущей admin-сессией."""

    password: str


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


async def require_admin(
    session: Annotated[AsyncSession, Depends(get_session)],
    admin_session: Annotated[str | None, Cookie(alias=_SESSION_COOKIE)] = None,
) -> int:
    """FastAPI-зависимость: вернуть operator_id из admin-сессии или 401.

    Подпись валидна — не значит «доступ есть навсегда»: отдельно перепроверяем
    `is_operator` (config/settings.py), чтобы убранный из списка операторов ID
    не мог продолжать ходить по старой, ещё не истёкшей сессии (spec 2026-08-31,
    см. докстринг модуля `services/admin_auth`). Проверка по списку в памяти,
    в БД не ходит.

    С аудита 2026-09-01 проверяется ещё и граница отзыва (`sessions_valid_from`).
    Из-за неё зависимость ходит в БД — раньше не ходила принципиально. Обмен
    осознанный: без чтения границы отзыв сессий невозможен в принципе, а операторов
    в системе единицы.
    """
    if not admin_session:
        raise HTTPException(status_code=401, detail="admin_auth_required")
    settings = get_settings()
    operator_id = verify_admin_session(admin_session, settings.secret_key.get_secret_value())
    if operator_id is None or not settings.is_operator(operator_id):
        raise HTTPException(status_code=401, detail="admin_auth_required")
    valid_from = await get_operator_sessions_valid_from(session, DEFAULT_ACCOUNT_ID, operator_id)
    if valid_from is not None and (
        verify_admin_session(
            admin_session, settings.secret_key.get_secret_value(), valid_from=valid_from
        )
        is None
    ):
        raise HTTPException(status_code=401, detail="admin_auth_required")
    return operator_id


@router.post("/authenticate", dependencies=[Depends(cabinet_auth_rate_limit)])
async def authenticate(data: AdminAuthIn, response: Response) -> OkResponse:
    """Обменять admin magic-link токен (из бота) на session-cookie."""
    operator_id = verify_admin_link(data.token, get_settings().secret_key.get_secret_value())
    if operator_id is None:
        raise HTTPException(status_code=401, detail="Ссылка недействительна или истекла")
    _set_admin_cookie(response, operator_id)
    return OkResponse()


@router.post("/login", dependencies=[Depends(cabinet_auth_rate_limit)])
async def login(
    data: AdminLoginIn,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OkResponse:
    """Возвратный вход в админку: Telegram ID + пароль → session-cookie.

    `is_operator` проверяется здесь же, ДО/наравне с паролем: пароль в БД мог
    пережить исключение оператора из `OPERATOR_TELEGRAM_IDS` (уволили — строку
    никто не чистит). Отказ по любой из двух причин выглядит одинаково —
    снаружи не должно быть видно, чем вызван 401.
    """
    ok = get_settings().is_operator(data.telegram_id) and await authenticate_operator(
        session, DEFAULT_ACCOUNT_ID, data.telegram_id, data.password
    )
    if not ok:
        raise HTTPException(status_code=401, detail="Не подходит номер или пароль")
    _set_admin_cookie(response, data.telegram_id)
    return OkResponse()


@router.post("/logout")
async def logout(response: Response) -> OkResponse:
    """Выход из админки — очистить session-cookie."""
    response.delete_cookie(_SESSION_COOKIE, path="/")
    return OkResponse()


@router.post("/logout-all")
async def logout_all(
    operator_id: Annotated[int, Depends(require_admin)],
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> OkResponse:
    """Отозвать все сессии оператора, включая текущую.

    Новую cookie НЕ выдаём: оператор просил выйти везде, а «везде» включает
    устройство, с которого он нажал кнопку.

    Строку оператора материализуем лениво (`get_or_create_operator`) перед отзывом:
    оператор мог до этого входить только по magic-link из бота и ещё не иметь
    строки в БД — без неё `revoke_operator_sessions` обновит 0 строк, граница
    останется `None`, и "выйти на всех устройствах" молча ничего не отзовёт.
    """
    await get_or_create_operator(session, DEFAULT_ACCOUNT_ID, operator_id)
    await revoke_operator_sessions(session, DEFAULT_ACCOUNT_ID, operator_id)
    await session.commit()
    response.delete_cookie(_SESSION_COOKIE, path="/")
    return OkResponse()


@router.get("/me")
async def me(operator_id: Annotated[int, Depends(require_admin)]) -> AdminMe:
    """Проверка сессии: вернуть operator_id (для дашборда)."""
    return AdminMe(operator_id=operator_id)


@router.post("/password")
async def set_password(
    data: AdminPasswordIn,
    operator_id: Annotated[int, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    response: Response,
) -> OkResponse:
    """Поставить/сменить пароль оператора текущей admin-сессии."""
    try:
        await set_operator_password(session, DEFAULT_ACCOUNT_ID, operator_id, data.password)
    except WeakPasswordError as exc:
        raise HTTPException(
            status_code=422,
            detail="Пароль короче десяти символов — так его слишком просто подобрать",
        ) from exc
    await session.commit()
    # Граница поднята — прежние сессии мертвы, включая ту, из которой пришёл запрос.
    # Поэтому сразу выдаём новую: сменивший пароль остаётся в системе на этом
    # устройстве, а все остальные выпадают.
    _set_admin_cookie(response, operator_id)
    return OkResponse()
