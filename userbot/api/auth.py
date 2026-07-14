"""Авторизация юзербота: /auth/start, /auth/code, /auth/password (spec §6).

Флоу: start (телефон → phone_code_hash) → code (при 2FA → needs_password) →
password. Код/пароль в логи не пишем (§10) — маскирование на стороне бота.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from userbot.api import get_client
from userbot.telethon_client import AuthError, UserbotClient

router = APIRouter(prefix="/auth", tags=["auth"])

Client = Annotated[UserbotClient, Depends(get_client)]


class StartRequest(BaseModel):
    phone: str


class StartResponse(BaseModel):
    phone_code_hash: str


class CodeRequest(BaseModel):
    phone: str
    code: str
    phone_code_hash: str


class CodeResponse(BaseModel):
    ok: bool
    needs_password: bool


class PasswordRequest(BaseModel):
    password: str


class OkResponse(BaseModel):
    ok: bool


@router.post("/start", response_model=StartResponse)
async def auth_start(body: StartRequest, client: Client) -> StartResponse:
    phone_code_hash = await client.auth_start(body.phone)
    return StartResponse(phone_code_hash=phone_code_hash)


@router.post("/code", response_model=CodeResponse)
async def auth_code(body: CodeRequest, client: Client) -> CodeResponse:
    try:
        needs_password = await client.auth_code(body.phone, body.code, body.phone_code_hash)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from exc
    return CodeResponse(ok=True, needs_password=needs_password)


@router.post("/password", response_model=OkResponse)
async def auth_password(body: PasswordRequest, client: Client) -> OkResponse:
    try:
        await client.auth_password(body.password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=exc.code) from exc
    return OkResponse(ok=True)
