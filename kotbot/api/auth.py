"""Авторизация kotbot: /auth/start, /auth/code (spec §4.1).

Формы запросов/ответов — строго по спеке:
- `POST /auth/start` `{strategy, login, password}` → `{status: "ok"}` |
  `{status: "code_required", attempt_id, hint}` | 400 `{detail: <code>}`;
- `POST /auth/code` `{attempt_id, code}` → `{status: "ok"}` | 400 `{detail: <code>}`.

Пароль и код в логи не пишутся — маскирование на стороне бота (§5).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from kotbot.api import get_automation
from kotbot.service import AuthError, KotbotAutomation
from kotbot.store import NotConfiguredError

router = APIRouter(prefix="/auth", tags=["auth"])

Automation = Annotated[KotbotAutomation, Depends(get_automation)]


class StartRequest(BaseModel):
    strategy: str  # "email" | "vk"
    login: str
    password: str


class CodeRequest(BaseModel):
    attempt_id: str
    code: str


@router.post("/start")
async def auth_start(body: StartRequest, automation: Automation) -> dict[str, str]:
    try:
        result = await automation.auth_start(body.strategy, body.login, body.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from exc
    except NotConfiguredError as exc:
        raise HTTPException(status_code=400, detail="not_configured") from exc
    if result.status == "ok":
        return {"status": "ok"}
    return {
        "status": "code_required",
        "attempt_id": result.attempt_id or "",
        "hint": result.hint,
    }


@router.post("/code")
async def auth_code(body: CodeRequest, automation: Automation) -> dict[str, str]:
    try:
        await automation.auth_code(body.attempt_id, body.code)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from exc
    except NotConfiguredError as exc:
        raise HTTPException(status_code=400, detail="not_configured") from exc
    return {"status": "ok"}
