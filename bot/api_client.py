"""HTTP-клиент бота к внутреннему API ядра.

Бот — тонкий клиент: за данными ходит сюда, не в БД (§1.3 CLAUDE.md). Ошибки сети
и 5xx превращаются в `CoreUnavailable`, чтобы хендлеры показали дружелюбную
заглушку вместо падения.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Any

import httpx
from config.settings import get_settings

_TIMEOUT = httpx.Timeout(10.0)
# Операции юзербота, которые реально ходят в Telegram: перебор точек на сервере
# ограничен своим бюджетом, и клиентский таймаут обязан быть больше — иначе оператор
# увидит «сервис недоступен» вместо осмысленной ошибки.
_USERBOT_AUTH_TIMEOUT = httpx.Timeout(120.0)
_USERBOT_PROBE_TIMEOUT = httpx.Timeout(120.0)

# Загрузка креатива тянет за собой создание кампании на площадке (кабинет,
# ad_plan/ad_group/banner, модерация) — ждём заметно дольше обычного (spec §5).
_LAUNCH_TIMEOUT = httpx.Timeout(360.0)


class CoreUnavailable(RuntimeError):
    """Ядро недоступно (сеть/таймаут/5xx) — показать заглушку оператору."""


class ContactNotRecognized(RuntimeError):
    """Ядро не распознало контакт (422) — оператору нужен корректный ввод."""


class BriefNotFound(RuntimeError):
    """Ядро не нашло бриф (404) — показать оператору «бриф не найден»."""


class CreativeRejected(RuntimeError):
    """Ядро отклонило креатив (422) — валидация медиа/текста или неполный бриф.

    `reason` — уже человекочитаемая причина для оператора.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class CampaignNotFound(RuntimeError):
    """Ядро не нашло кампанию (404) — показать оператору «кампания не найдена»."""


class CampaignStopFailed(RuntimeError):
    """Площадка/канал не приняли остановку (502) — это не «ядро лежит»."""


class UserbotUnavailable(RuntimeError):
    """Юзербот-сервис не сконфигурирован или недоступен — работаем в мок-режиме."""


class UserbotAuthError(RuntimeError):
    """Юзербот отверг код/пароль (400) — показать оператору причину, дать повтор."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class KotbotUnavailable(RuntimeError):
    """Kotbot-сервис не сконфигурирован или недоступен — работаем в мок-режиме."""


class KotbotAuthError(RuntimeError):
    """Kotbot-сервис отверг вход (400) — показать оператору подсказку по коду."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class InviteItem:
    """Строка трекинга брифа (зеркало `InviteItem` ядра)."""

    contact: str
    variant: str
    channel: str
    sent_at: str | None
    received_at: str | None
    waiting_days: int
    contact_name: str | None = None
    brief_id: int | None = None


@dataclass(frozen=True, slots=True)
class InviteCreated:
    """Итог создания инвайта (зеркало `CreateInviteOut` ядра, сценарии §8.1)."""

    invite_id: int
    status: str  # sent | failed
    channel: str  # telegram | email | manual
    fallback_text: str | None
    error: str | None
    # Известный email клиента при сорвавшейся доставке в Telegram; None — нет такого.
    fallback_email: str | None = None


@dataclass(frozen=True, slots=True)
class BriefFieldItem:
    """Одно нумерованное поле карточки брифа (зеркало `BriefFieldOut` ядра)."""

    n: int
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class BriefCard:
    """Карточка брифа для оператора (зеркало `BriefCardOut` ядра)."""

    brief_id: int
    variant: str
    status: str
    client_name: str | None
    client_email: str | None
    client_phone: str | None
    client_telegram: str | None
    fields: list[BriefFieldItem]
    has_creative: bool
    campaign_status: str | None
    # Распознанная площадка подписки — приходит из ядра готовой строкой.
    surface_title: str = ""
    surface_needs_creative: bool = True


@dataclass(frozen=True, slots=True)
class CreativeResult:
    """Итог приёма креатива и подготовки/запуска РК (зеркало `CreativeLaunchOut` ядра)."""

    campaign_status: str
    campaign_id: int
    message: str


@dataclass(frozen=True, slots=True)
class CampaignStopped:
    """Итог остановки кампании (зеркало `CampaignStopOut` ядра).

    `external_id is None` — кампании не было на площадке: статус сменили у себя,
    останавливать во внешней системе было нечего.
    """

    campaign_id: int
    status: str
    external_id: str | None


@dataclass(frozen=True, slots=True)
class CabinetItem:
    """Карточка кабинета (зеркало `CabinetItem` ядра)."""

    id: str
    name: str
    status: str
    launched_at: str
    is_mock: bool


@dataclass(frozen=True, slots=True)
class CabinetStats:
    """Метрики кабинета за период (зеркало `StatsOut` ядра)."""

    cabinet_id: str
    period: str
    shows: float
    clicks: float
    spent: float
    results: float
    ctr: float
    cpc: float
    is_mock: bool


@dataclass(frozen=True, slots=True)
class AdAccountItem:
    """Рекламный кабинет оператора (зеркало `AdAccountOut` ядра).

    Токена здесь нет и быть не может — только хвост из четырёх символов.
    """

    id: int
    title: str
    external_id: str
    username: str | None
    token_tail: str
    advertiser_kind: str
    advertiser_name: str | None
    advertiser_inn: str | None
    status: str
    health: str
    health_checked_at: str | None
    health_error: str | None
    balance_rub: str | None
    is_usable: bool


class AdAccountRejected(RuntimeError):
    """Ядро отказалось заводить кабинет — причина уже пригодна для показа."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AdAccountNotFound(RuntimeError):
    """Кабинета нет (404) — вероятно, его уже удалили из другого окна."""


def _base_url() -> str:
    return get_settings().core_base_url.rstrip("/")


async def _get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET к ядру; сетевые ошибки/5xx → `CoreUnavailable`."""
    url = f"{_base_url()}/api/v1{path}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise CoreUnavailable(str(exc)) from exc
    result: dict[str, Any] = response.json()
    return result


def _to_invites(payload: dict[str, Any]) -> list[InviteItem]:
    return [InviteItem(**item) for item in payload.get("items", [])]


async def get_pending() -> list[InviteItem]:
    """Инвайты, доставленные клиенту, но ещё не вернувшиеся брифом."""
    return _to_invites(await _get("/invites", {"status": "pending"}))


async def get_recent() -> list[InviteItem]:
    """Инвайты, по которым бриф пришёл за последнюю неделю."""
    return _to_invites(await _get("/invites", {"status": "recent"}))


def _parse_card(payload: dict[str, Any]) -> BriefCard:
    client = payload.get("client") or {}
    return BriefCard(
        brief_id=int(payload["brief_id"]),
        variant=str(payload["variant"]),
        status=str(payload["status"]),
        client_name=client.get("full_name"),
        client_email=client.get("email"),
        client_phone=client.get("phone"),
        client_telegram=client.get("telegram"),
        fields=[
            BriefFieldItem(n=int(f["n"]), label=str(f["label"]), value=str(f["value"]))
            for f in payload.get("fields", [])
        ],
        has_creative=bool(payload.get("has_creative", False)),
        campaign_status=payload.get("campaign_status"),
        surface_title=str(payload.get("surface_title") or ""),
        surface_needs_creative=bool(payload.get("surface_needs_creative", True)),
    )


async def get_brief(brief_id: int) -> BriefCard:
    """`GET /briefs/{id}`: карточка брифа. 404 → `BriefNotFound`; сеть/5xx → `CoreUnavailable`."""
    url = f"{_base_url()}/api/v1/briefs/{brief_id}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.get(url)
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise CoreUnavailable(str(exc)) from exc
    if response.status_code == 404:
        raise BriefNotFound(str(brief_id))
    if response.status_code >= 500:
        raise CoreUnavailable(f"core {response.status_code}")
    return _parse_card(response.json())


async def update_brief(brief_id: int, edits: dict[int, str]) -> tuple[BriefCard, list[int]]:
    """`PATCH /briefs/{id}`: применить правки, вернуть карточку + неизвестные номера."""
    url = f"{_base_url()}/api/v1/briefs/{brief_id}"
    body = {"edits": {str(number): value for number, value in edits.items()}}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.patch(url, json=body)
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise CoreUnavailable(str(exc)) from exc
    if response.status_code == 404:
        raise BriefNotFound(str(brief_id))
    if response.status_code >= 500:
        raise CoreUnavailable(f"core {response.status_code}")
    payload = response.json()
    unknown = [int(n) for n in payload.get("unknown", [])]
    return _parse_card(payload), unknown


def _creative_reject_reason(detail: Any) -> str:
    """Человекочитаемая причина отказа по 422-детали ядра."""
    if isinstance(detail, dict):
        issues = detail.get("issues")
        if issues:
            return " ".join(str(item) for item in issues)
        missing = detail.get("missing")
        if missing:
            return (
                "Бриф заполнен не полностью — не хватает полей: "
                + ", ".join(str(item) for item in missing)
                + ". Внесите правки и повторите."
            )
    if detail == "goal_not_supported":
        return "Эта цель рекламы ещё не реализована. Пока доступны «Подписчики»."
    return "Креатив не принят. Проверьте файл и текст."


async def upload_creative(
    brief_id: int,
    media_b64: str,
    media_type: str,
    width: int,
    height: int,
    title: str,
    body: str,
    ad_account_id: int | None = None,
    goal: str | None = None,
) -> CreativeResult:
    """`POST /briefs/{id}/creative`: отправить креатив (триггер запуска РК).

    `ad_account_id`/`goal` — выбор оператора, сделанный до загрузки материалов.

    404 → `BriefNotFound`; 413/422 → `CreativeRejected`; сеть/5xx → `CoreUnavailable`.
    """
    url = f"{_base_url()}/api/v1/briefs/{brief_id}/creative"
    payload: dict[str, Any] = {
        "media_b64": media_b64,
        "media_type": media_type,
        "width": width,
        "height": height,
        "title": title,
        "body": body,
        "ad_account_id": ad_account_id,
        "goal": goal,
    }
    try:
        async with httpx.AsyncClient(timeout=_LAUNCH_TIMEOUT) as client:
            response = await client.post(url, json=payload)
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise CoreUnavailable(str(exc)) from exc
    if response.status_code == 404:
        raise BriefNotFound(str(brief_id))
    if response.status_code == 413:
        raise CreativeRejected("Файл слишком большой (до 20 МБ).")
    if response.status_code == 422:
        detail: Any = None
        with contextlib.suppress(ValueError):
            detail = response.json().get("detail")
        raise CreativeRejected(_creative_reject_reason(detail))
    if response.status_code == 409:
        # Кабинет выбран, но токеном воспользоваться нельзя (удалён, сменился ключ).
        raise CreativeRejected(
            "Рекламный кабинет недоступен: токен стёрт или кабинет удалён. "
            "Выберите другой кабинет или добавьте его заново через /cabinets."
        )
    if response.status_code >= 500:
        raise CoreUnavailable(f"core {response.status_code}")
    data = response.json()
    return CreativeResult(
        campaign_status=str(data["campaign_status"]),
        campaign_id=int(data["campaign_id"]),
        message=str(data["message"]),
    )


async def launch_brief(brief_id: int) -> CreativeResult:
    """`POST /briefs/{id}/launch`: запустить кампанию без креатива.

    Для площадок, где объявлением служит сам объект (пост, клип, трек). Ошибки
    разбираются так же, как у загрузки креатива: 404 → `BriefNotFound`,
    422 → `CreativeRejected`, сеть/5xx → `CoreUnavailable`.
    """
    url = f"{_base_url()}/api/v1/briefs/{brief_id}/launch"
    try:
        async with httpx.AsyncClient(timeout=_LAUNCH_TIMEOUT) as client:
            response = await client.post(url, json={})
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise CoreUnavailable(str(exc)) from exc
    if response.status_code == 404:
        raise BriefNotFound(str(brief_id))
    if response.status_code == 422:
        detail: Any = None
        with contextlib.suppress(ValueError):
            detail = response.json().get("detail")
        raise CreativeRejected(_creative_reject_reason(detail))
    if response.status_code >= 500:
        raise CoreUnavailable(f"core {response.status_code}")
    data = response.json()
    return CreativeResult(
        campaign_status=str(data["campaign_status"]),
        campaign_id=int(data["campaign_id"]),
        message=str(data["message"]),
    )


async def stop_campaign(campaign_id: int) -> CampaignStopped:
    """`POST /campaigns/{id}/stop`: остановить кампанию на площадке.

    404 → `CampaignNotFound`; 502 (канал не принял остановку) → `CampaignStopFailed`;
    прочие 5xx и сеть → `CoreUnavailable`.
    """
    url = f"{_base_url()}/api/v1/campaigns/{campaign_id}/stop"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url)
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise CoreUnavailable(str(exc)) from exc
    if response.status_code == 404:
        raise CampaignNotFound(str(campaign_id))
    if response.status_code == 502:
        raise CampaignStopFailed(str(campaign_id))
    if response.status_code >= 500:
        raise CoreUnavailable(f"core {response.status_code}")
    payload = response.json()
    return CampaignStopped(
        campaign_id=int(payload["campaign_id"]),
        status=str(payload["status"]),
        external_id=payload.get("external_id"),
    )


async def get_cabinets() -> list[CabinetItem]:
    """Список рекламных кабинетов (реальные или демо)."""
    payload = await _get("/cabinets")
    return [CabinetItem(**item) for item in payload.get("items", [])]


async def get_cabinet_stats(cabinet_id: str, period: str) -> CabinetStats:
    """Метрики кабинета за период (`all`/`month`/`week`)."""
    payload = await _get(f"/cabinets/{cabinet_id}/stats", {"period": period})
    return CabinetStats(**payload)


# Создание инвайта включает доставку (userbot до 15с / SMTP до 20с) — таймаут
# заметно больше обычного _TIMEOUT.
_INVITE_TIMEOUT = httpx.Timeout(30.0)


async def create_invite(variant: str, contact: str, operator_telegram_id: int) -> InviteCreated:
    """`POST /invites`: токен, запись BriefInvite, доставка выбранным каналом.

    422 (нераспознанный контакт) → `ContactNotRecognized`; сеть/5xx → `CoreUnavailable`.
    """
    url = f"{_base_url()}/api/v1/invites"
    body = {
        "variant": variant,
        "contact": contact,
        "operator_telegram_id": operator_telegram_id,
    }
    try:
        async with httpx.AsyncClient(timeout=_INVITE_TIMEOUT) as client:
            response = await client.post(url, json=body)
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise CoreUnavailable(str(exc)) from exc
    if response.status_code == 422:
        raise ContactNotRecognized("contact not recognized")
    if response.status_code >= 500:
        raise CoreUnavailable(f"core {response.status_code}")
    payload = response.json()
    return InviteCreated(
        invite_id=int(payload["invite_id"]),
        status=str(payload["status"]),
        channel=str(payload["channel"]),
        fallback_text=payload.get("fallback_text"),
        error=payload.get("error"),
        fallback_email=payload.get("fallback_email"),
    )


# --- Юзербот (Telethon-сервис доставки, spec §9) ----------------------------
#
# Ходим напрямую в userbot-сервис (свой BASE_URL), не через ядро. Пустой
# `USERBOT_BASE_URL` = сервис не сконфигурирован → `UserbotUnavailable`
# (хендлер уходит в мок-режим). 400 от auth-эндпоинтов = неверный код/пароль.
# Сессии — по операторам: во все вызовы передаём sender_id (Telegram ID оператора).


@dataclass(frozen=True, slots=True)
class UserbotHealth:
    """Состояние сессии оператора (зеркало ответа userbot-сервиса).

    `state` различает «до Telegram не достучались» (`unreachable` — восстановится
    само) и «ключ мёртв» (`expired` — нужна перепривязка). Свести их в один флаг
    значит давать оператору неверный совет.
    """

    authorized: bool
    phone: str | None = None
    error: str | None = None
    state: str = "unknown"
    endpoint: str | None = None

    @property
    def unreachable(self) -> bool:
        """Сессию не видно из-за сети, а не из-за разлогина."""
        return self.state == "unreachable" or self.error == "unreachable"


def _userbot_base_url() -> str:
    return get_settings().userbot_base_url.rstrip("/")


def userbot_configured() -> bool:
    """Задан ли `USERBOT_BASE_URL` — иначе хендлер работает в мок-режиме."""
    return bool(_userbot_base_url())


async def _userbot_request(
    method: str,
    path: str,
    json: dict[str, Any] | None = None,
    limit: httpx.Timeout | None = None,
) -> dict[str, Any]:
    """Запрос к userbot-сервису; сеть/5xx → `UserbotUnavailable`, 400 → `UserbotAuthError`.

    Таймаут по умолчанию рассчитан на чтение состояния из памяти. Операции, которые
    реально ходят в сеть (авторизация, форсированная проверка), передают свой —
    он должен быть больше серверного бюджета перебора, иначе клиент отвалится
    раньше, чем сервис успеет ответить осмысленной ошибкой.
    """
    base = _userbot_base_url()
    if not base:
        raise UserbotUnavailable("userbot_base_url is empty")
    try:
        async with httpx.AsyncClient(timeout=limit or _TIMEOUT) as client:
            response = await client.request(method, f"{base}{path}", json=json)
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise UserbotUnavailable(str(exc)) from exc
    if response.status_code == 400:
        detail = "unknown"
        with contextlib.suppress(ValueError):
            detail = str(response.json().get("detail", "unknown"))
        raise UserbotAuthError(detail)
    if response.status_code >= 500:
        raise UserbotUnavailable(f"userbot {response.status_code}")
    result: dict[str, Any] = response.json()
    return result


async def userbot_status(sender_id: int) -> UserbotHealth:
    """Опрос `/health?sender_id=`: авторизована ли сессия оператора, номер."""
    payload = await _userbot_request("GET", f"/sessions/{sender_id}")
    return _to_health(payload)


def _to_health(payload: dict[str, Any]) -> UserbotHealth:
    return UserbotHealth(
        authorized=bool(payload.get("authorized", False)),
        phone=payload.get("phone"),
        error=payload.get("error"),
        state=str(payload.get("state", "unknown")),
        endpoint=payload.get("endpoint"),
    )


async def userbot_health_all() -> dict[int, UserbotHealth]:
    """Состояние всех сессий по операторам (для фонового поллера и диагностики).

    Читается из памяти сервиса — запрос дешёвый, сеть при нём не трогается.
    """
    payload = await _userbot_request("GET", "/sessions")
    sessions = payload.get("sessions", [])
    return {int(item["sender_id"]): _to_health(item) for item in sessions}


async def userbot_probe(sender_id: int) -> UserbotHealth:
    """Форсированная проверка сессии по сети («проверить сейчас»)."""
    payload = await _userbot_request(
        "POST", f"/sessions/{sender_id}/probe", limit=_USERBOT_PROBE_TIMEOUT
    )
    return _to_health(payload)


async def userbot_endpoints() -> list[dict[str, Any]]:
    """Матрица достижимости точек подключения изнутри контейнера."""
    payload = await _userbot_request("GET", "/diagnostics/endpoints", limit=_USERBOT_PROBE_TIMEOUT)
    items = payload.get("endpoints", [])
    return [dict(item) for item in items]


async def userbot_start_auth(sender_id: int, phone: str) -> str:
    """`/auth/start` — вернуть `phone_code_hash` для последующего ввода кода."""
    payload = await _userbot_request(
        "POST",
        "/auth/start",
        {"sender_id": sender_id, "phone": phone},
        limit=_USERBOT_AUTH_TIMEOUT,
    )
    return str(payload["phone_code_hash"])


async def userbot_submit_code(sender_id: int, phone: str, code: str, phone_code_hash: str) -> bool:
    """`/auth/code` — вернуть `needs_password` (нужен ли ввод пароля 2FA)."""
    payload = await _userbot_request(
        "POST",
        "/auth/code",
        {
            "sender_id": sender_id,
            "phone": phone,
            "code": code,
            "phone_code_hash": phone_code_hash,
        },
        limit=_USERBOT_AUTH_TIMEOUT,
    )
    return bool(payload.get("needs_password", False))


async def userbot_submit_password(sender_id: int, password: str) -> None:
    """`/auth/password` — завершить авторизацию при включённом 2FA."""
    await _userbot_request(
        "POST",
        "/auth/password",
        {"sender_id": sender_id, "password": password},
        limit=_USERBOT_AUTH_TIMEOUT,
    )


# --- Kotbot (браузерная автоматизация kotbot.ru, spec 2026-07-17 §5) ---------
#
# Ходим напрямую в kotbot-сервис (свой BASE_URL), не через ядро. Пустой
# `KOTBOT_BASE_URL` = сервис не сконфигурирован → `KotbotUnavailable`
# (хендлер уходит в мок-режим). 400 от auth-эндпоинтов = код ошибки в detail.

# Браузерный логин долгий (навигация + челлендж) — таймаут заметно больше обычного.
_KOTBOT_AUTH_TIMEOUT = httpx.Timeout(120.0)


@dataclass(frozen=True, slots=True)
class KotbotStrategyHealth:
    """Состояние одной стратегии входа (зеркало `/health.strategies.<name>`)."""

    has_credentials: bool
    has_state: bool
    needs_reauth: bool


@dataclass(frozen=True, slots=True)
class KotbotHealth:
    """Агрегированное здоровье kotbot-сервиса (зеркало `GET /health`)."""

    healthy: bool
    email: KotbotStrategyHealth
    vk: KotbotStrategyHealth


@dataclass(frozen=True, slots=True)
class KotbotAuthResult:
    """Итог `/auth/start`: ok либо code_required с attempt_id и подсказкой."""

    status: str  # "ok" | "code_required"
    attempt_id: str | None
    hint: str


def _kotbot_base_url() -> str:
    return get_settings().kotbot_base_url.rstrip("/")


def kotbot_configured() -> bool:
    """Задан ли `KOTBOT_BASE_URL` — иначе хендлер работает в мок-режиме."""
    return bool(_kotbot_base_url())


async def _kotbot_request(
    method: str,
    path: str,
    json: dict[str, Any] | None = None,
    *,
    request_timeout: httpx.Timeout = _TIMEOUT,
) -> dict[str, Any]:
    """Запрос к kotbot-сервису; сеть/5xx → `KotbotUnavailable`, 400 → `KotbotAuthError`."""
    base = _kotbot_base_url()
    if not base:
        raise KotbotUnavailable("kotbot_base_url is empty")
    try:
        async with httpx.AsyncClient(timeout=request_timeout) as client:
            response = await client.request(method, f"{base}{path}", json=json)
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise KotbotUnavailable(str(exc)) from exc
    if response.status_code == 400:
        detail = "unknown"
        with contextlib.suppress(ValueError):
            detail = str(response.json().get("detail", "unknown"))
        raise KotbotAuthError(detail)
    if response.status_code >= 500:
        raise KotbotUnavailable(f"kotbot {response.status_code}")
    result: dict[str, Any] = response.json()
    return result


def _parse_strategy_health(payload: dict[str, Any]) -> KotbotStrategyHealth:
    return KotbotStrategyHealth(
        has_credentials=bool(payload.get("has_credentials", False)),
        has_state=bool(payload.get("has_state", False)),
        needs_reauth=bool(payload.get("needs_reauth", False)),
    )


async def kotbot_status() -> KotbotHealth:
    """Опрос `GET /health`: агрегат healthy + состояние по стратегиям."""
    payload = await _kotbot_request("GET", "/health")
    strategies = payload.get("strategies", {})
    return KotbotHealth(
        healthy=bool(payload.get("healthy", False)),
        email=_parse_strategy_health(strategies.get("email", {})),
        vk=_parse_strategy_health(strategies.get("vk", {})),
    )


async def kotbot_start_auth(strategy: str, login: str, password: str) -> KotbotAuthResult:
    """`/auth/start` — начать вход; ok либо code_required с attempt_id."""
    payload = await _kotbot_request(
        "POST",
        "/auth/start",
        {"strategy": strategy, "login": login, "password": password},
        request_timeout=_KOTBOT_AUTH_TIMEOUT,
    )
    return KotbotAuthResult(
        status=str(payload.get("status", "")),
        attempt_id=payload.get("attempt_id"),
        hint=str(payload.get("hint", "")),
    )


async def kotbot_submit_code(attempt_id: str, code: str) -> None:
    """`/auth/code` — донести код подтверждения; успех = без исключений."""
    await _kotbot_request(
        "POST",
        "/auth/code",
        {"attempt_id": attempt_id, "code": code},
        request_timeout=_KOTBOT_AUTH_TIMEOUT,
    )


# --- рекламные кабинеты (spec 2026-07-27 §10) --------------------------------


def _to_ad_account(payload: dict[str, Any]) -> AdAccountItem:
    return AdAccountItem(
        id=int(payload["id"]),
        title=str(payload["title"]),
        external_id=str(payload["external_id"]),
        username=payload.get("username"),
        token_tail=str(payload.get("token_tail", "")),
        advertiser_kind=str(payload.get("advertiser_kind", "owner")),
        advertiser_name=payload.get("advertiser_name"),
        advertiser_inn=payload.get("advertiser_inn"),
        status=str(payload.get("status", "active")),
        health=str(payload.get("health", "unknown")),
        health_checked_at=payload.get("health_checked_at"),
        health_error=payload.get("health_error"),
        balance_rub=payload.get("balance_rub"),
        is_usable=bool(payload.get("is_usable", False)),
    )


async def list_ad_accounts() -> list[AdAccountItem]:
    """`GET /ad-accounts`: рекламные кабинеты оператора (без токенов)."""
    payload = await _get("/ad-accounts")
    return [_to_ad_account(item) for item in payload.get("items", [])]


_AD_ACCOUNT_ERRORS = {
    "invalid_token": "VK не принял этот токен. Проверьте, что скопирован весь `access_token`.",
    "duplicate_account": "Такой кабинет уже добавлен.",
    "vk_unreachable": "VK сейчас не отвечает. Попробуйте ещё раз через минуту.",
    "encryption_key_missing": (
        "На сервере не задан ключ шифрования VK_ADS_SECRET_KEY — "
        "без него токен негде хранить. Нужна помощь администратора."
    ),
}


async def add_ad_account(
    token: str,
    *,
    title: str | None = None,
    advertiser_kind: str = "owner",
    advertiser_name: str | None = None,
    advertiser_inn: str | None = None,
) -> AdAccountItem:
    """`POST /ad-accounts`: добавить кабинет по токену.

    Токен уходит только сюда и обратно не возвращается. Понятные отказы ядра
    (битый токен, дубль, VK лежит) превращаются в `AdAccountRejected`.
    """
    url = f"{_base_url()}/api/v1/ad-accounts"
    payload: dict[str, Any] = {
        "token": token,
        "title": title,
        "advertiser_kind": advertiser_kind,
        "advertiser_name": advertiser_name,
        "advertiser_inn": advertiser_inn,
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url, json=payload)
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise CoreUnavailable(str(exc)) from exc
    if response.status_code in (400, 409, 422, 500, 503):
        detail = ""
        with contextlib.suppress(ValueError):
            detail = str(response.json().get("detail", ""))
        raise AdAccountRejected(
            _AD_ACCOUNT_ERRORS.get(detail, "Не получилось добавить кабинет, проверьте токен.")
        )
    if response.status_code >= 500:
        raise CoreUnavailable(f"core {response.status_code}")
    return _to_ad_account(response.json())


async def check_ad_account(ad_account_id: int) -> AdAccountItem:
    """`POST /ad-accounts/{id}/check`: проверить токен прямо сейчас."""
    url = f"{_base_url()}/api/v1/ad-accounts/{ad_account_id}/check"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(url)
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise CoreUnavailable(str(exc)) from exc
    if response.status_code == 404:
        raise AdAccountNotFound(str(ad_account_id))
    if response.status_code >= 500:
        raise CoreUnavailable(f"core {response.status_code}")
    return _to_ad_account(response.json())


async def delete_ad_account(ad_account_id: int) -> None:
    """`DELETE /ad-accounts/{id}`: удалить кабинет (токен стирается безвозвратно)."""
    url = f"{_base_url()}/api/v1/ad-accounts/{ad_account_id}"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.delete(url)
    except (httpx.HTTPError, httpx.TransportError) as exc:
        raise CoreUnavailable(str(exc)) from exc
    if response.status_code == 404:
        raise AdAccountNotFound(str(ad_account_id))
    if response.status_code >= 500:
        raise CoreUnavailable(f"core {response.status_code}")
