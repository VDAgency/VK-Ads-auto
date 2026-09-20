"""Резолв числового id объекта рекламы ВК по короткому (vanity) адресу.

Короткий адрес (`vk.ru/fin_dolm`) сам по себе не говорит, сообщество это или
личная страница — это решает только числовой id (`club…`/`id…`). Разметка
публичной страницы отдаёт `"owner_id":…` — отрицательный у сообщества,
положительный у личной страницы, — но живой прогон 2026-09-20
(`vk.com/fin_dolm`, обычный User-Agent, HTTP 200) показал, что разметка личной
страницы несёт СРАЗУ несколько `owner_id`, и ПЕРВОЕ вхождение — decoy-ноль:
12 вхождений, первое — `0`, остальные 11 — верный `808632468`. «Первое
совпадение решает всё» на этой разметке резолвило бы страницу в id `0`. Берём
ВСЕ вхождения, отбрасываем нули и берём самое частое из оставшихся (при
равной частоте — самое раннее по порядку) — так decoy-ноль не мешает.
`"user_id"` в качестве запасного маркера сюда сознательно НЕ включён: та же
разведка нашла на странице сообщества (`vk.com/club228817082`) decoy
`"user_id":100` — доверять `user_id`, когда `owner_id` в разметке не нашёлся
вовсе, нельзя, поэтому при отсутствии ненулевых `owner_id` резолв просто
деградирует в `None`.

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
    """Разобрать ВСЕ вхождения `owner_id`, отбросить нули (decoy на личной
    странице, живой прогон 2026-09-20) и взять самое частое из оставшихся
    значений — при равной частоте побеждает то, что встретилось раньше. Знак
    решает тип (отрицательный — сообщество), модуль — числовой id. Ни одного
    ненулевого `owner_id` — `None`: `user_id` намеренно не запасной маркер (на
    странице сообщества у него встречается decoy `"user_id":100`, доверять
    ему без owner_id нельзя)."""
    matches = [int(value) for value in _OWNER_ID_RE.findall(html)]
    non_zero = [value for value in matches if value != 0]
    if not non_zero:
        return None

    counts: dict[int, int] = {}
    order: list[int] = []
    for value in non_zero:
        if value not in counts:
            order.append(value)
        counts[value] = counts.get(value, 0) + 1

    # `max` возвращает ПЕРВЫЙ максимум при равенстве — порядок `order` уже
    # соответствует «раньше встретилось» среди уникальных значений.
    winner = max(order, key=lambda value: counts[value])
    if winner < 0:
        return ResolvedVkObject(numeric_id=-winner, kind="community")
    return ResolvedVkObject(numeric_id=winner, kind="personal")


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
        logger.warning("VK object resolution found no usable owner_id marker for %r", url)
    return resolved
