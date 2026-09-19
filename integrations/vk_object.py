"""Резолв числового id объекта рекламы ВК по короткому (vanity) адресу.

Короткий адрес (`vk.ru/fin_dolm`) сам по себе не говорит, сообщество это или
личная страница — это решает только числовой id (`club…`/`id…`). Разведка
2026-07-26: обычный HTTP GET публичной страницы отдаёт HTML с маркером
`"owner_id":…` — отрицательный у сообщества, положительный у личной страницы;
у личной страницы дополнительно встречается `"user_id":…` — запасной маркер на
случай, если `owner_id` в разметке не нашёлся.

Резолвер вызывается только при запуске кампании (`services/launch_service.py`),
не при приёме брифа — приём не должен зависеть от доступности vk.com. Любая
неудача (сеть, таймаут, не-200 ответ, отсутствие маркера, чужой хост) — не
исключение, а `None`: вызывающая сторона остаётся при прежнем поведении
(подсказка брифа → «сообщество»). HTML страницы в лог не пишем (CLAUDE.md §1.1)
— только факт неудачи.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

# Резолв имеет смысл только для ВК — короткие адреса других площадок (ОК и т.д.)
# сюда не попадают: там пара «сообщество/страница» решается только подсказкой брифа.
_ALLOWED_HOSTS = frozenset({"vk.com", "vk.ru", "m.vk.com"})

_COMMUNITY_RE = re.compile(r"^(?:club|public|event)(\d+)$")
_PROFILE_RE = re.compile(r"^id(\d+)$")
_OWNER_ID_RE = re.compile(r'"owner_id"\s*:\s*(-?\d+)')
_USER_ID_RE = re.compile(r'"user_id"\s*:\s*(-?\d+)')

_TIMEOUT_SECONDS = 5.0
# Обычный браузерный User-Agent — без него разметка публичной страницы может отличаться.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class ResolvedVkObject:
    """Итог резолва: числовой id объекта и его тип."""

    numeric_id: int
    kind: Literal["community", "personal"]

    @property
    def canonical_url(self) -> str:
        """Числовая форма адреса — её уже понимает `integrations.vk_api.resolve_ad_object`
        и она перевешивает там подсказку брифа."""
        if self.kind == "personal":
            return f"https://vk.com/id{self.numeric_id}"
        return f"https://vk.com/club{self.numeric_id}"


def _with_scheme(url: str) -> str:
    raw = url.strip()
    return raw if "//" in raw else f"https://{raw}"


def _host(url: str) -> str:
    return urlsplit(_with_scheme(url)).netloc.lower()


def _slug(url: str) -> str:
    """Первый сегмент пути ссылки: `club228817082`, `id777`, короткий адрес."""
    segments = [segment for segment in urlsplit(_with_scheme(url)).path.split("/") if segment]
    return segments[0].lower() if segments else ""


def _from_numeric_slug(slug: str) -> ResolvedVkObject | None:
    community = _COMMUNITY_RE.match(slug)
    if community:
        return ResolvedVkObject(numeric_id=int(community.group(1)), kind="community")
    profile = _PROFILE_RE.match(slug)
    if profile:
        return ResolvedVkObject(numeric_id=int(profile.group(1)), kind="personal")
    return None


def _from_html(html: str) -> ResolvedVkObject | None:
    """Первое вхождение `owner_id` решает всё — знак определяет тип. `user_id` —
    запасной маркер личной страницы на случай отсутствия `owner_id` в разметке."""
    owner_match = _OWNER_ID_RE.search(html)
    if owner_match:
        owner_id = int(owner_match.group(1))
        if owner_id < 0:
            return ResolvedVkObject(numeric_id=-owner_id, kind="community")
        return ResolvedVkObject(numeric_id=owner_id, kind="personal")

    user_match = _USER_ID_RE.search(html)
    if user_match:
        return ResolvedVkObject(numeric_id=abs(int(user_match.group(1))), kind="personal")

    return None


async def resolve_vk_object(
    url: str, *, client: httpx.AsyncClient | None = None
) -> ResolvedVkObject | None:
    """Узнать числовой id объекта рекламы по ссылке ВК.

    Уже числовой адрес (`club…`/`public…`/`event…`/`id…`) резолвится без сети.
    Хост не ВК — сразу `None`, без запроса. Сетевая ошибка, не-200 ответ,
    отсутствие маркера в разметке — тоже `None` + `logger.warning` (без HTML
    в логе); неудача резолва не должна ронять запуск кампании.
    """
    if _host(url) not in _ALLOWED_HOSTS:
        return None

    numeric = _from_numeric_slug(_slug(url))
    if numeric is not None:
        return numeric

    owns_client = client is None
    http = client or httpx.AsyncClient()
    try:
        response = await http.get(
            _with_scheme(url),
            timeout=_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
    except httpx.HTTPError as exc:
        logger.warning("VK object resolution failed for %r: %s", url, type(exc).__name__)
        return None
    finally:
        if owns_client:
            await http.aclose()

    if response.status_code != 200:
        logger.warning("VK object resolution got HTTP %s for %r", response.status_code, url)
        return None

    resolved = _from_html(response.text)
    if resolved is None:
        logger.warning("VK object resolution found no owner_id/user_id marker for %r", url)
    return resolved
