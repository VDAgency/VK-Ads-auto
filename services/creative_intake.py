"""Приём креатива: декод base64 → валидация → сохранение → запуск РК (общий сервис).

Общая логика для операторского эндпоинта бота (`core/api/v1/briefs.py`) и админки
(`core/api/v1/admin_data.py`), чтобы не дублировать декод/валидацию/сохранение/запуск.
Ошибки — типизированные (`CreativeError`), маппинг в HTTP-коды делает роутер.
"""

from __future__ import annotations

import base64
import binascii

from config.settings import Settings
from sqlalchemy.ext.asyncio import AsyncSession

from services.creative_store import save_creative
from services.creative_validate import (
    ImageCreative,
    is_valid,
    validate_image,
    validate_text,
    validate_video_size,
)
from services.launch_service import LaunchOutcome, launch_from_creative

# Защитный предел размера тела (бот качает из Telegram файлы ≤20 МБ).
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class CreativeError(Exception):
    """Проблема с креативом. `code`: `media_b64_invalid` | `media_too_large` | `invalid`.

    Для `invalid` в `issues` — список понятных оператору проблем (валидация медиа/текста).
    """

    def __init__(self, code: str, issues: list[str] | None = None) -> None:
        self.code = code
        self.issues = issues or []
        super().__init__(code)


async def intake_creative(
    session: AsyncSession,
    account_id: int,
    brief_id: int,
    *,
    media_b64: str,
    media_type: str,
    width: int,
    height: int,
    title: str,
    body: str,
    settings: Settings | None = None,
) -> LaunchOutcome:
    """Декодировать, валидировать, сохранить креатив и подготовить/запустить кампанию.

    Бросает `CreativeError` (битый base64 / слишком большой / невалидный), а также
    `BriefNotFoundError` / `BriefValidationError` из `launch_from_creative`.
    """
    try:
        raw = base64.b64decode(media_b64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise CreativeError("media_b64_invalid") from exc
    if len(raw) > MAX_UPLOAD_BYTES:
        raise CreativeError("media_too_large")

    if media_type == "photo":
        issues = validate_image(
            ImageCreative(fmt="jpg", width=width, height=height, size_bytes=len(raw))
        )
    else:
        issues = validate_video_size(len(raw))
    issues += validate_text(title, body)
    if not is_valid(issues):
        raise CreativeError("invalid", issues)

    path = save_creative(brief_id, media_type, raw)
    return await launch_from_creative(
        session,
        account_id,
        brief_id,
        media_type,
        path,
        title or None,
        body or None,
        settings=settings,
    )
