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
from integrations.vk_surfaces import Surface, surface_for, text_limit
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


async def _brief_surface(session: AsyncSession, account_id: int, brief_id: int) -> Surface | None:
    """Площадка брифа (`integrations/vk_surfaces.py`) для проверок хэштегов.

    `None` — брифа нет: `HashtagError("not_supported")` в этом случае не имеет
    смысла бросать здесь, `launch_from_creative` ниже честно ответит
    `BriefNotFoundError` сама.
    """
    brief = await get_brief(session, account_id, brief_id)
    if brief is None:
        return None
    kind = parse_target_type(brief.payload.get("target_type", "")).value
    return surface_for(kind)


def _hashtag_text_limit(surface: Surface | None) -> int:
    """Лимит текста для дописывания хэштегов: самый узкий текстовый слот площадки.

    Площадка уже известна на этом шаге (см. `_brief_surface`) — значит нет нужды
    ждать выбора конкретного шаблона (он зависит от соотношения сторон креатива
    и решается позже, в `launch_from_creative`): берём консервативный минимум по
    ВСЕМ её шаблонам (`integrations/vk_surfaces.py::text_limit`, тот же хелпер,
    которым площадка пользуется при сборке кампании) — с хэштегами текст не
    попадёт ни в один слот площадки уже здесь, а не молча обрежется в
    `integrations.vk_api._fit` при реальной отправке в VK. Площадка без шаблонов
    (продвижение поста/клипа/трека) сюда не доходит — её отсекает проверка
    `needs_creative` до вызова этой функции; `None`/пустые шаблоны — защитный
    фолбэк на `MAX_TEXT_LEN`. Итог не может быть шире `MAX_TEXT_LEN`, которым
    продолжает пользоваться существующая `validate_text` ниже.
    """
    if surface is None or not surface.patterns:
        return MAX_TEXT_LEN
    floor = min(text_limit(pattern.text_slot) for pattern in surface.patterns)
    return min(floor, MAX_TEXT_LEN)


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
    валидации длины текста. Лимит — самый узкий текстовый слот площадки брифа
    (`_hashtag_text_limit`, не шире `MAX_TEXT_LEN`), чтобы текст с хэштегами не
    обрезался молча в `integrations.vk_api._fit` при реальной отправке в VK: у
    каналов ВК/MAX это `text_90`, у Дзена — `text_40` (`integrations/
    vk_surfaces.py:TEXT_SLOT_LIMITS`, конкретный шаблон выбирается позже —
    внутри `launch_from_creative` → `services.mapping.build_campaign_spec`, по
    соотношению сторон уже сохранённого креатива, но сама площадка и набор её
    шаблонов известны уже здесь, из брифа). Площадка вовсе без текстового слота
    (продвижение готового поста/клипа/трека, `Surface.needs_creative is False`)
    хэштеги не принимает — `HashtagError("not_supported")`.

    Бросает `CreativeError` (битый base64 / слишком большой / невалидный),
    `HashtagError` (нормализация/лимит хэштегов), а также `BriefNotFoundError` /
    `BriefValidationError` / `UnsupportedGoalError` и ошибки выбора кабинета из
    `launch_from_creative`.
    """
    tags = normalize_hashtags(hashtags or "")
    hashtag_limit = MAX_TEXT_LEN
    if tags:
        surface = await _brief_surface(session, account_id, brief_id)
        if surface is not None and not surface.needs_creative:
            raise HashtagError("not_supported")
        hashtag_limit = _hashtag_text_limit(surface)

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
        body = append_hashtags(body, tags, hashtag_limit)
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
