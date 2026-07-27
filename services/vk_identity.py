"""Опознание рекламного кабинета VK Ads по токену (spec 2026-07-27 §2.1, §8.2).

Отдельный узкий модуль, а не метод `VkApiAdapter`: во-первых, по адаптеру идёт
параллельная работа и трогать его нельзя; во-вторых, здесь нужен ровно один
read-only запрос, который служит сразу двум задачам — опознать кабинет при
добавлении и проверить, жив ли токен.

Живой ответ VK на `GET /user.json?fields=…` (проверено 2026-07-27):

    id: 10000001
    username: "a1b2c3d4e5@agency_client"
    additional_info.client_name: "Студия «Пример»"
    status: "active"

Поэтому оператору достаточно вставить токен — остальное берём отсюда.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Тот же хост, что у `VkApiAdapter` (ads.vk.com == target.my.com).
VK_API_BASE = "https://ads.vk.com/api/v2"

# Ровно те поля, что нужны карточке кабинета. Просить больше незачем: VK
# отвечает ошибкой на неизвестное поле, а `permissions` — сотни строк.
_IDENTITY_FIELDS = "id,username,additional_info,status"

# VK держит 3 rps; ждать дольше нескольких секунд смысла нет — оператор
# смотрит на экран и ждёт ответа.
_TIMEOUT = 15.0


class VkIdentityError(Exception):
    """Базовая ошибка опознания кабинета."""


class InvalidTokenError(VkIdentityError):
    """Токен отклонён VK (401/403): отозван, просрочен или введён с опечаткой."""


class VkUnreachableError(VkIdentityError):
    """VK недоступен (сеть, таймаут, 5xx) — про сам токен ничего не известно."""


@dataclass(frozen=True, slots=True)
class VkIdentity:
    """Кто мы в VK по этому токену."""

    external_id: str
    username: str | None
    title: str
    status: str


def _title_from(payload: dict[str, object], fallback_id: str) -> str:
    """Человекочитаемое имя кабинета; при пустом — подставляем id, а не пустую строку."""
    info = payload.get("additional_info")
    if isinstance(info, dict):
        name = info.get("client_name") or info.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    username = payload.get("username")
    if isinstance(username, str) and username.strip():
        return username.strip()
    return f"Кабинет {fallback_id}"


async def fetch_identity(token: str, *, client: httpx.AsyncClient | None = None) -> VkIdentity:
    """Опознать кабинет по токену. Он же health-check (spec §7).

    Бросает `InvalidTokenError` (токен не принят) или `VkUnreachableError`
    (не дозвонились). Разделение принципиальное: в первом случае кабинет
    помечается `unauthorized`, во втором — `error`, потому что про токен
    по-прежнему ничего не известно.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        response = await http.get(
            f"{VK_API_BASE}/user.json",
            params={"fields": _IDENTITY_FIELDS},
            headers={"Authorization": f"Bearer {token}"},
        )
    except httpx.HTTPError as exc:
        # Текст исключения не содержит токен (он уходит только в заголовке).
        raise VkUnreachableError(f"VK request failed: {type(exc).__name__}") from exc
    finally:
        if owns_client:
            await http.aclose()

    if response.status_code in (401, 403):
        raise InvalidTokenError("VK rejected the token")
    if response.status_code >= 400:
        raise VkUnreachableError(f"VK returned HTTP {response.status_code}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise VkUnreachableError("VK returned a non-JSON body") from exc
    if not isinstance(payload, dict):
        raise VkUnreachableError("VK returned an unexpected body")

    raw_id = payload.get("id")
    if raw_id is None:
        raise VkUnreachableError("VK response has no account id")
    external_id = str(raw_id)

    username = payload.get("username")
    status = payload.get("status")
    return VkIdentity(
        external_id=external_id,
        username=username if isinstance(username, str) else None,
        title=_title_from(payload, external_id),
        status=status if isinstance(status, str) else "unknown",
    )


async def fetch_balance(token: str, *, client: httpx.AsyncClient | None = None) -> str | None:
    """Баланс кабинета (`/user/account.json` → `balance`), справочно.

    Любая ошибка — `None`: баланс приятен, но ради него нельзя ронять ни
    добавление кабинета, ни health-check.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        response = await http.get(
            f"{VK_API_BASE}/user/account.json",
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status_code >= 400:
            return None
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        logger.debug("balance lookup failed", exc_info=True)
        return None
    finally:
        if owns_client:
            await http.aclose()

    if not isinstance(payload, dict):
        return None
    balance = payload.get("balance")
    return balance if isinstance(balance, str) else None
