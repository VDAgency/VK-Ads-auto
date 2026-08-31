"""VK Ads OAuth2 — агентский доступ поверх `POST /api/v2/oauth2/token.json`.

⚠️ Хост API — `ads.vk.com` (тот же, что в `integrations/vk_api.py` и
`services/vk_identity.py`), а НЕ `ads.vk.ru` — это домен только веб-интерфейса
(см. `docs/superpowers/plans/2026-08-25-agency-cabinets.md` §A1, где хостом
ошибочно назван `ads.vk.ru`; живая документация VK и остальной код проекта
согласны на `ads.vk.com`).

⚠️ Тело запроса — `application/x-www-form-urlencoded`, НЕ JSON: так документирован
именно этот эндпоинт (в отличие от прочих `/api/v2/*.json`, которые принимают JSON,
см. `vk_api.py`).

Грант `agency_client_credentials` — нестандартное расширение OAuth2: агентство
выпускает токен на кабинет клиента БЕЗ подтверждения самим клиентом. Идентификатор
клиента передаётся ровно одним из двух способов: `agency_client_id` (числовой id)
или `agency_client_name` (имя пользователя клиента) — какой под рукой, такой и
используем.

Лимит VK: не более пяти живых токенов на пару «приложение + пользователь»
(`POST /api/v2/oauth2/token/delete.json` снимает лимит).

Модуль ничего не знает про БД/модели ядра: учётные данные и ссылки на клиента —
только параметры функций, результат — обычная структура. Хранением и шифрованием
занимается сервисный слой (вне этого модуля).

Секреты (`client_secret`, `access_token`, `refresh_token`) никогда не попадают в
текст исключений и логи — только код ошибки VK (`error`) или имя исключения
транспорта, как это уже сделано в `services/vk_identity.py`.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from pydantic import SecretStr

logger = logging.getLogger(__name__)

# Тот же хост, что у VkApiAdapter (integrations/vk_api.py) и fetch_identity
# (services/vk_identity.py) — ads.vk.com, не путать с ads.vk.ru (веб-интерфейс).
VK_OAUTH_BASE = "https://ads.vk.com/api/v2"
TOKEN_URL = f"{VK_OAUTH_BASE}/oauth2/token.json"
TOKEN_DELETE_URL = f"{VK_OAUTH_BASE}/oauth2/token/delete.json"

GRANT_AGENCY_CLIENT = "agency_client_credentials"
GRANT_OWN_ACCOUNT = "client_credentials"
GRANT_REFRESH_TOKEN = "refresh_token"

# VK держит 3 rps; это не браузерный флоу, ждать дольше нескольких секунд смысла
# нет (тот же бюджет, что у health-check в vk_identity.py).
_TIMEOUT = 30.0

# `error` из тела ответа, которым VK помечает именно неверные client_id/secret
# (см. живую документацию: «An 'invalid_client' error can occur due to an
# incorrect client_id or client_secret, or if the OAuth2 client is blocked»).
_INVALID_CLIENT_CODE = "invalid_client"


class VkOAuthError(Exception):
    """Базовая ошибка OAuth-клиента VK Ads."""


class VkOAuthNotConfigured(VkOAuthError):
    """`client_id`/`client_secret` агентства не заданы (пустые настройки).

    Означает «агентский доступ не настроен» — не пытаемся стучаться в VK
    с заведомо пустыми учётными данными.
    """


class VkOAuthInvalidCredentials(VkOAuthError):
    """VK отклонил сами `client_id`/`client_secret` (код `invalid_client`, 401/403).

    Отличается от `VkOAuthRejected`: здесь неверно приложение целиком, повторять
    запрос с теми же учётными данными бессмысленно.
    """


class VkOAuthRejected(VkOAuthError):
    """VK отверг запрос по существу (например `invalid_grant`): неверная ссылка
    на клиента агентства, просроченный/отозванный `refresh_token`, превышен лимит
    токенов и т.п. Учётные данные приложения при этом верны.
    """


class VkOAuthUnavailable(VkOAuthError):
    """VK недоступен или ответил непонятно (сеть, таймаут, 5xx, битое тело).

    Про сами учётные данные и токен ничего не известно — вызывающий код решает,
    повторять ли попытку (см. решение «две попытки» в плане A1/B2).
    """


@dataclass(frozen=True, slots=True)
class VkOAuthToken:
    """Токен, выпущенный VK Ads. Секреты — только через `SecretStr`.

    `expires_at` посчитан от момента получения ответа (VK отдаёт `expires_in` —
    сутки в секундах), а не от произвольного места в коде вызывающей стороны:
    сервисному слою решать, когда обновлять, здесь только факт истечения.
    """

    access_token: SecretStr
    refresh_token: SecretStr
    expires_at: datetime
    token_type: str = "bearer"


def _require_configured(client_id: str, client_secret: str) -> None:
    """Пустые `client_id`/`client_secret` — «агентский доступ не настроен»."""
    if not client_id.strip() or not client_secret.strip():
        raise VkOAuthNotConfigured("vk_ads_client_id/vk_ads_client_secret are not configured")


async def _post_form(
    url: str, data: dict[str, str], *, client: httpx.AsyncClient | None = None
) -> httpx.Response:
    """Отправить `application/x-www-form-urlencoded` тело; сеть → `VkOAuthUnavailable`."""
    try:
        if client is not None:
            return await client.post(url, data=data)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as owned:
            return await owned.post(url, data=data)
    except httpx.HTTPError as exc:
        # Текст исключения не содержит данных запроса (только имя типа) — секреты
        # (client_secret, refresh_token) в data сюда не попадают.
        raise VkOAuthUnavailable(f"VK OAuth request failed: {type(exc).__name__}") from exc


def _error_code(response: httpx.Response) -> str:
    """Машинный код ошибки VK (`error`) из тела; пусто, если тела/поля нет.

    Намеренно берём только короткий код, не `error_description` и не тело
    целиком — этого достаточно для диагностики и безопасно для лога/исключения.
    """
    with contextlib.suppress(ValueError):
        payload = response.json()
        if isinstance(payload, dict):
            code = payload.get("error")
            if isinstance(code, str):
                return code
    return ""


def _raise_for_status(response: httpx.Response) -> None:
    """Ответ VK → типизированное исключение (или ничего на 2xx)."""
    if response.status_code < 400:
        return
    code = _error_code(response)
    if code == _INVALID_CLIENT_CODE or response.status_code in (401, 403):
        logger.warning("VK OAuth rejected client credentials: %s", code or response.status_code)
        raise VkOAuthInvalidCredentials(
            f"VK rejected the client credentials ({code or response.status_code})"
        )
    if response.status_code >= 500:
        raise VkOAuthUnavailable(f"VK returned HTTP {response.status_code}")
    logger.warning("VK OAuth request rejected: %s", code or response.status_code)
    raise VkOAuthRejected(f"VK refused the request: {code or f'HTTP {response.status_code}'}")


def _json_object(response: httpx.Response) -> dict[str, Any]:
    """Тело успешного ответа как dict; иначе `VkOAuthUnavailable` (см. vk_identity.py)."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise VkOAuthUnavailable("VK returned a non-JSON body") from exc
    if not isinstance(payload, dict):
        raise VkOAuthUnavailable("VK returned an unexpected body")
    return payload


def _as_seconds(value: Any) -> int:
    """`expires_in` VK отдаёт строкой («86400») — приводим к int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise VkOAuthUnavailable("VK response has no valid expires_in") from None


def _parse_token(payload: dict[str, Any]) -> VkOAuthToken:
    """Тело `oauth2/token.json` → `VkOAuthToken`. Момент отсчёта — «сейчас»."""
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    if not isinstance(access, str) or not access:
        raise VkOAuthUnavailable("VK response has no access_token")
    if not isinstance(refresh, str) or not refresh:
        raise VkOAuthUnavailable("VK response has no refresh_token")
    expires_in = _as_seconds(payload.get("expires_in"))
    token_type = payload.get("token_type")
    return VkOAuthToken(
        access_token=SecretStr(access),
        refresh_token=SecretStr(refresh),
        expires_at=datetime.now(UTC) + timedelta(seconds=expires_in),
        token_type=token_type if isinstance(token_type, str) and token_type else "bearer",
    )


async def request_agency_client_token(
    client_id: str,
    client_secret: str,
    *,
    agency_client_id: str | None = None,
    agency_client_name: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> VkOAuthToken:
    """Выпустить токен на кабинет клиента агентства — БЕЗ подтверждения клиента.

    `grant_type=agency_client_credentials`. Ровно один из `agency_client_id`
    (числовой id) / `agency_client_name` (имя пользователя клиента) — какой есть
    под рукой у вызывающей стороны, такой и используется.

    Бросает `VkOAuthNotConfigured` (пустые учётные данные приложения),
    `ValueError` (не задан ни один из способов идентификации клиента или заданы
    оба сразу — ошибка вызывающего кода, а не VK), `VkOAuthInvalidCredentials`
    (неверные `client_id`/`client_secret`), `VkOAuthRejected` (например, неверный
    `agency_client_id`/`agency_client_name` — приложение верное, клиент не найден)
    или `VkOAuthUnavailable` (сеть/5xx/битый ответ).
    """
    _require_configured(client_id, client_secret)
    if bool(agency_client_id) == bool(agency_client_name):
        raise ValueError("provide exactly one of agency_client_id or agency_client_name")

    data = {
        "grant_type": GRANT_AGENCY_CLIENT,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    if agency_client_id:
        data["agency_client_id"] = agency_client_id
    else:
        data["agency_client_name"] = agency_client_name or ""

    response = await _post_form(TOKEN_URL, data, client=client)
    _raise_for_status(response)
    token = _parse_token(_json_object(response))
    logger.info("VK agency client token issued, expires_at=%s", token.expires_at.isoformat())
    return token


async def request_own_account_token(
    client_id: str,
    client_secret: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> VkOAuthToken:
    """Выпустить токен СОБСТВЕННОГО аккаунта агентства (`grant_type=client_credentials`).

    Ни клиента, ни ссылки на него здесь нет — только `client_id`/`client_secret`
    приложения. Живая документация VK называет этот грант «Client Credentials
    Grant — доступ к данным собственного аккаунта» — им, а не долгоживущим
    токеном из окружения, агентство удостоверяет само себя перед агентским
    эндпоинтом (`POST /agency/clients.json`, см. `services.agency_cabinets`):
    одна и та же пара ключей приложения покрывает и это, и выпуск токенов на
    кабинеты клиентов (`request_agency_client_token` выше), так что вставлять
    в `.env` токен вручную не нужно вовсе.

    Бросает `VkOAuthNotConfigured` (пустые учётные данные приложения),
    `VkOAuthInvalidCredentials` (неверные `client_id`/`client_secret`),
    `VkOAuthRejected` (VK отверг запрос по существу) или `VkOAuthUnavailable`
    (сеть/5xx/битый ответ) — та же карта, что у `request_agency_client_token`,
    минус `ValueError`: идентифицировать здесь некого, кроме самого приложения.
    """
    _require_configured(client_id, client_secret)

    data = {
        "grant_type": GRANT_OWN_ACCOUNT,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    response = await _post_form(TOKEN_URL, data, client=client)
    _raise_for_status(response)
    token = _parse_token(_json_object(response))
    logger.info("VK own account token issued, expires_at=%s", token.expires_at.isoformat())
    return token


async def refresh_agency_token(
    refresh_token: str,
    client_id: str,
    client_secret: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> VkOAuthToken:
    """Обновить токен по ключу обновления (`grant_type=refresh_token`).

    Та же карта исключений, что у `request_agency_client_token`, минус
    `ValueError` про способ идентификации клиента (её здесь нет) — вместо неё
    `ValueError`, если `refresh_token` пуст. Просроченный/отозванный
    `refresh_token` VK отклоняет как `invalid_grant` → `VkOAuthRejected`.
    """
    _require_configured(client_id, client_secret)
    if not refresh_token:
        raise ValueError("refresh_token is required")

    data = {
        "grant_type": GRANT_REFRESH_TOKEN,
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    response = await _post_form(TOKEN_URL, data, client=client)
    _raise_for_status(response)
    token = _parse_token(_json_object(response))
    logger.info("VK agency token refreshed, expires_at=%s", token.expires_at.isoformat())
    return token


async def delete_agency_tokens(
    client_id: str,
    client_secret: str,
    username: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> None:
    """Удалить все живые токены пользователя (`POST /oauth2/token/delete.json`).

    Пригодится при отказе `VkOAuthRejected` из-за лимита VK — не более пяти
    живых токенов на пару «приложение + пользователь»: без удаления следующий
    выпуск токена тем же клиентом будет упираться в лимит.

    Ответ VK на удаление пуст по контракту — тело не парсим (по аналогии с
    `stop()`/`delete_campaign()` в `integrations/vk_api.py`).
    """
    _require_configured(client_id, client_secret)
    if not username:
        raise ValueError("username is required")

    data = {"client_id": client_id, "client_secret": client_secret, "username": username}
    response = await _post_form(TOKEN_DELETE_URL, data, client=client)
    _raise_for_status(response)
    logger.info("VK agency tokens deleted for a user")
