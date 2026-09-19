"""Приём креатива: декод base64 → валидация → сохранение → запуск РК (общий сервис).

Общая логика для операторского эндпоинта бота (`core/api/v1/briefs.py`) и админки
(`core/api/v1/admin_data.py`), чтобы не дублировать декод/валидацию/сохранение/запуск.
Ошибки — типизированные (`CreativeError`), маппинг в HTTP-коды делает роутер.
"""

from __future__ import annotations

import base64
import binascii

from config.settings import Settings
from db.repositories import get_brief
from integrations.vk_surfaces import surface_for
from sqlalchemy.ext.asyncio import AsyncSession

from services.brief_parser import parse_target_type
from services.creative_store import save_creative
from services.creative_validate import (
    MAX_TEXT_LEN,
    ImageCreative,
    is_valid,
    validate_image,
    validate_text,
    validate_video_size,
)
from services.hashtags import HashtagError, append_hashtags, normalize_hashtags
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


async def launch_without_creative(
    session: AsyncSession,
    account_id: int,
    brief_id: int,
    *,
    settings: Settings | None = None,
    ad_account_id: int | None = None,
    goal: str | None = None,
) -> LaunchOutcome:
    """Запустить кампанию без креатива — для площадок, которым он не нужен.

    Продвижение готового поста, клипа или трека: объявлением служит сам объект, и
    просить у оператора картинку не за чем. Бросает `UnsupportedGoalError`, если
    площадка брифа креатив всё-таки требует, — молча запускать пустое объявление
    там, где нужен макет, нельзя.
    """
    return await launch_from_creative(
        session,
        account_id,
        brief_id,
        media_type="",
        file_path=None,
        title=None,
        body=None,
        settings=settings,
        ad_account_id=ad_account_id,
        goal=goal,
    )


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
    hashtags: str | None = None,
    settings: Settings | None = None,
    ad_account_id: int | None = None,
    goal: str | None = None,
) -> LaunchOutcome:
    """Декодировать, валидировать, сохранить креатив и подготовить/запустить кампанию.

    `ad_account_id` и `goal` — выбор оператора (рекламный кабинет и цель). Оба
    необязательны: без них поведение прежнее, поэтому старые вызовы не ломаются.

    `hashtags` — необязательная строка тегов оператора (`services/hashtags.py`):
    нормализуется и дописывается в конец текста объявления ДО существующей
    валидации длины текста. Лимит — тот же generic `MAX_TEXT_LEN`, которым уже
    пользуется `validate_text` здесь: сама рекламная площадка (и её текстовый
    слот/лимит) на этом шаге ещё не выбрана — выбор шаблона происходит позже,
    внутри `launch_from_creative` → `services.mapping.build_campaign_spec`, по
    соотношению сторон уже сохранённого креатива. Заводить для хэштегов
    отдельный, более узкий лимит тут значило бы дублировать источник правды
    вместо переиспользования (`integrations/vk_surfaces.py:TEXT_SLOT_LIMITS`
    используется там же, где и раньше — при сборке кампании).
    Площадка вовсе без текстового слота (продвижение готового поста/клипа/
    трека, `Surface.needs_creative is False`) хэштеги не принимает — `HashtagError
    ("not_supported")`.

    Бросает `CreativeError` (битый base64 / слишком большой / невалидный),
    `HashtagError` (нормализация/лимит хэштегов), а также `BriefNotFoundError` /
    `BriefValidationError` / `UnsupportedGoalError` и ошибки выбора кабинета из
    `launch_from_creative`.
    """
    tags = normalize_hashtags(hashtags or "")
    if tags:
        brief = await get_brief(session, account_id, brief_id)
        if brief is not None:
            kind = parse_target_type(brief.payload.get("target_type", "")).value
            if not surface_for(kind).needs_creative:
                raise HashtagError("not_supported")

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
    if tags:
        body = append_hashtags(body, tags, MAX_TEXT_LEN)
    issues += validate_text(title, body)
    if not is_valid(issues):
        raise CreativeError("invalid", issues)

    path = save_creative(brief_id, media_type, raw)
    return await launch_from_creative(
        session,
        account_id,
        brief_id,
        media_type=media_type,
        file_path=path,
        title=title or None,
        body=body or None,
        settings=settings,
        ad_account_id=ad_account_id,
        goal=goal,
    )
