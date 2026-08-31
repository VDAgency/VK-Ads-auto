"""VkApiAdapter — прямой VK Ads API (myTarget v2) через httpx.

Иерархия кампании в терминах API: `ad_plan` (цель, даты) → `campaigns` (пакет,
таргетинг, бюджет, автобиддинг) → `banners` (медиа, тексты, ссылка на объект
рекламы). То, что бриф и ядро зовут «группой объявлений», в теле запроса
называется `campaigns[]`; `/ad_groups.json` — эндпоинт только для чтения, id
совпадают (`ad_plan.campaigns[].id == ad_group.id`).

⚠️ Создаётся всё ОДНИМ вложенным `POST /ad_plans.json`: отдельный запрос на план
отвечает HTTP 400 `campaigns: required` (боевая проверка 2026-07-26). Поля и уровни
подтверждены живыми запросами — см. docs/VK_API_REFERENCE.md. Пункты, помеченные
VERIFY, требуют проверки мутацией на минимальном бюджете.

Адаптер мутаций НЕ вызывается автоматически; запуск идёт через оркестрацию, которую
мы контролируем. Токен берётся из per-account конфигурации и никогда не логируется.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr
from services.brief_parser import TargetType
from services.mapping import CampaignSpec

from integrations.adapter import PlatformAdapter
from integrations.vk_creative_formats import (
    fit_to_slot,
    is_video,
    pattern_for_creative,
)
from integrations.vk_geo import VkGeoResolver
from integrations.vk_surfaces import (
    CONTENT_SLOTS,
    ICON_SLOT,
    SLOT_ABOUT_COMPANY,
    SLOT_TITLE,
    Pattern,
    Surface,
    slot_size,
    surface_for,
    text_limit,
)
from integrations.vk_video import image_to_video

logger = logging.getLogger(__name__)

BASE_URL = "https://ads.vk.com/api/v2"

# Автобиддинг под цель «подписчики» (у эталонной группы max_goals).
AUTOBIDDING_MAX_GOALS = "max_goals"
# Остановка кампании в VK — перевод статуса в `blocked`.
STATUS_ACTIVE = "active"
STATUS_BLOCKED = "blocked"
# Удаление кампании — ОТДЕЛЬНЫЙ статус, не путать с `blocked` (боевая проверка
# 2026-08-23, см. docs/VK_API_REFERENCE.md).
STATUS_DELETED = "deleted"

# Загрузка медиа: у картинок и видео РАЗНЫЕ эндпоинты. Ролик, отправленный в статику,
# отвергается как `format_not_supported` (боевая проверка 2026-07-27).
STATIC_UPLOAD_PATH = "/content/static.json"
VIDEO_UPLOAD_PATH = "/content/video.json"

# Лимит заголовка одинаков у всех площадок; лимит текста зависит от шаблона
# (у каналов VK/MAX это `text_90`, а не привычный `text_2000`).
TITLE_MAX_LEN = text_limit(SLOT_TITLE)

# Исключаем уже подписанных — обязательный таргетинг для цели «подписчики».
NOT_GROUP_MEMBER = "not_group_member"
# «Возраст неизвестен»: без нуля в age_list теряется часть аудитории.
AGE_UNKNOWN = 0

_COMMUNITY_RE = re.compile(r"^(?:club|public|event)(\d+)$")
_PROFILE_RE = re.compile(r"^id(\d+)$")

_BASE_METRICS = ("shows", "clicks", "spent", "ctr", "cpc", "cpm")

# Язык справочника регионов: VK отдаёт русские названия только по Accept-Language.
REGIONS_LANGUAGE = "ru"


@dataclass(frozen=True)
class AdObject:
    """Объект рекламы: ссылка плюс площадка, из которой следуют пакет и цель VK."""

    url: str
    url_object_id: str | None
    surface: Surface

    @property
    def package_id(self) -> int:
        return self.surface.package_id

    @property
    def objective(self) -> str:
        return self.surface.objective

    @property
    def is_community(self) -> bool:
        """Сообщество ли это — от этого зависит таргетинг «исключить подписчиков»."""
        return self.surface.kind in (
            TargetType.COMMUNITY.value,
            TargetType.OK_COMMUNITY.value,
        )


def _object_slug(object_url: str) -> str:
    """Первый сегмент пути ссылки: `club228817082`, `id777`, короткий адрес."""
    raw = object_url.strip()
    if "//" not in raw:
        raw = f"https://{raw}"
    segments = [segment for segment in urlsplit(raw).path.split("/") if segment]
    return segments[0].lower() if segments else ""


# Площадки, которые числовой адрес ВК способен опровергнуть: club…/id… однозначно
# говорят, сообщество это или страница. Остальные площадки (рассылка, каналы, ОК)
# из адреса не выводятся вовсе — там подсказка брифа единственный источник.
_URL_DECIDABLE = frozenset({TargetType.COMMUNITY.value, TargetType.PERSONAL_PAGE.value})


def resolve_ad_object(object_url: str, kind: str = "") -> AdObject:
    """Определить площадку подписки и вытекающие из неё пакет и цель кампании.

    Числовой адрес ВК — проверяемый факт и перевешивает подсказку брифа, но только в
    паре «сообщество или страница»: `vk.com/club…`, `public…`, `event…` против
    `vk.com/id…`. Рассылку, каналы и Одноклассники адрес не выражает, поэтому там
    решает бриф. Короткий адрес (`vk.ru/fin_dolm`) без подсказки — сообщество, самый
    частый случай брифа.
    """
    url = object_url.strip()
    slug = _object_slug(url)

    profile = _PROFILE_RE.match(slug)
    community = _COMMUNITY_RE.match(slug)
    match = profile or community
    object_id = match.group(1) if match else None
    from_url = TargetType.PERSONAL_PAGE.value if profile else TargetType.COMMUNITY.value

    if match and (not kind or kind in _URL_DECIDABLE):
        if kind and kind != from_url:
            logger.warning(
                "Brief says %r but numeric url %r says %r — trusting url", kind, url, from_url
            )
        return AdObject(url, object_id, surface_for(from_url))

    if kind:
        return AdObject(url, object_id, surface_for(kind))

    if "ok.ru" in url.lower():
        guessed = TargetType.OK_PROFILE if "/profile/" in url else TargetType.OK_COMMUNITY
        logger.warning("Odnoklassniki url %r without brief hint: assuming %s", url, guessed.value)
        return AdObject(url, None, surface_for(guessed.value))

    logger.warning("Vanity url %r without brief hint: assuming VK community", url)
    return AdObject(url, None, surface_for(TargetType.COMMUNITY.value))


def campaign_objective(spec: CampaignSpec) -> str:
    """Objective для ad_plan: у каждой площадки подписки он свой.

    `services/mapping.py` отдаёт обобщённый `socialengagement` для любого брифа,
    а цель зависит от площадки (`vk_miniapps`, `odkl`, `max_channel`, …) — правим
    здесь, где площадка уже известна.
    """
    resolved = resolve_ad_object(spec.object_url, spec.object_kind).objective
    if resolved != spec.objective:
        logger.info("Objective %r from spec replaced with %r", spec.objective, resolved)
    return resolved


def creative_pattern(
    spec: CampaignSpec, creative_ref: str, *, prefer_video: bool = False
) -> Pattern:
    """Шаблон объявления под присланный креатив и площадку из спеки."""
    return pattern_for_creative(
        resolve_ad_object(spec.object_url, spec.object_kind).surface,
        creative_ref,
        prefer_video=prefer_video,
    )


def _fit(text: str, limit: int) -> str:
    """Обрезать текст под лимит слота VK — площадка длиннее не принимает."""
    return text[:limit]


def _today() -> str:
    """Дата старта кампании в формате VK (`YYYY-MM-DD`)."""
    return date.today().isoformat()


# Ресурс агентских клиентов (B1, план 2026-08-25-agency-cabinets.md): заводит клиента
# либо добавляет уже существующего. Контракт сверен с документацией VK
# (`ads.vk.ru/en/doc/api/resource/AgencyClients`; боевой хост API, как и везде в этом
# модуле, — `ads.vk.com`), боевым вызовом ЕЩЁ НЕ проверен — до подтверждения
# агентского аккаунта.
AGENCY_CLIENTS_PATH = "/agency/clients.json"
AGENCY_ACCESS_FULL = "full_access"
# Лимит VK на постраничный запрос списка клиентов (по умолчанию 20, максимум 50).
AGENCY_CLIENTS_LIMIT_DEFAULT = 20
AGENCY_CLIENTS_LIMIT_MAX = 50


class VkAgencyClientError(Exception):
    """Базовая ошибка операций с клиентами агентства VK (`/agency/clients.json`)."""


class VkAgencyClientForbidden(VkAgencyClientError):
    """VK отказал по правам (403): агентский статус не подтверждён либо нет права
    `create_clients`. Отличать от прочих отказов важно — вызывающему коду (B2) нужен
    внятный текст для оператора, а не общее «что-то пошло не так»."""


class VkAgencyClientValidationError(VkAgencyClientError):
    """VK отклонил тело запроса (400) — ошибка валидации полей."""


class VkAgencyClientNotFound(VkAgencyClientError):
    """VK не нашёл клиента (404) — например, неверный `user.id`/`user.username` при
    добавлении уже существующего клиента."""


class VkAgencyClientUnavailable(VkAgencyClientError):
    """VK недоступен либо ответил непонятно (сеть, 5xx, битое тело, пустой `items`)."""


@dataclass(frozen=True, slots=True)
class VkAgencyClient:
    """Клиент агентства VK — то, что понадобится дальше по цепочке (A1/B2).

    `client_id` и `username` оба годятся для `agency_client_id`/`agency_client_name`
    при выпуске токена (`integrations/vk_oauth.py::request_agency_client_token`);
    `username` критичен — именно он остаётся ссылкой на клиента, когда числовой id
    ещё не сохранён вызывающей стороной. `ad_account_id`/`balance` — из `user.account`,
    оба могут быть `None`, если рекламный аккаунт клиента ещё не создан площадкой.
    """

    client_id: str
    username: str | None
    ad_account_id: str | None
    balance: str | None
    status: str
    access_type: str


@dataclass(frozen=True, slots=True)
class VkAgencyClientPage:
    """Страница списка клиентов агентства (`GET /agency/clients.json`)."""

    items: list[VkAgencyClient]
    count: int
    limit: int
    offset: int


def _agency_error_detail(response: httpx.Response) -> str:
    """Короткий текст ошибки из тела ответа (если есть) — без секретов, для оператора."""
    with contextlib.suppress(ValueError):
        payload = response.json()
        if isinstance(payload, dict):
            for key in ("error_description", "error", "detail", "message"):
                value = payload.get(key)
                if isinstance(value, str) and value:
                    return value
    return ""


def _raise_for_agency_status(response: httpx.Response) -> None:
    """Ответ `/agency/clients.json` → типизированное исключение (см. справку VK).

    403 разбирается отдельно от прочих отказов: это единственный код, за которым
    стоит конкретная и действенная причина («подтвердите агентский аккаунт /
    выдайте право create_clients»), а не общая формулировка отказа.
    """
    if response.status_code < 400:
        return
    detail = _agency_error_detail(response)
    if response.status_code == 403:
        logger.warning("VK denied agency clients access (403): %s", detail or "no detail")
        raise VkAgencyClientForbidden(
            "VK denied access to agency clients (403): agency account not confirmed "
            "or missing the create_clients permission"
        )
    if response.status_code == 400:
        raise VkAgencyClientValidationError(f"VK rejected the request: {detail or 'HTTP 400'}")
    if response.status_code == 404:
        raise VkAgencyClientNotFound(f"VK could not find the agency client: {detail or 'HTTP 404'}")
    if response.status_code >= 500:
        raise VkAgencyClientUnavailable(f"VK returned HTTP {response.status_code}")
    raise VkAgencyClientUnavailable(
        f"VK returned HTTP {response.status_code}: {detail or 'no detail'}"
    )


def _agency_payload(response: httpx.Response) -> dict[str, Any]:
    """Тело успешного ответа `/agency/clients.json` как dict."""
    try:
        payload = response.json()
    except ValueError as exc:
        raise VkAgencyClientUnavailable("VK returned a non-JSON body") from exc
    if not isinstance(payload, dict):
        raise VkAgencyClientUnavailable("VK returned an unexpected body")
    return payload


def _agency_items(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Список `items[]` тела ответа как dict-ы; чужие/нестроковые элементы отбрасываются."""
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _as_str(value: Any) -> str | None:
    """Число или строка из ответа VK → строка; `None`, если поля нет/тип неожиданный.

    VK отдаёт часть числовых полей строками, часть — числами (та же непоследовательность,
    что и у метрик статистики, см. `_as_float`) — приводим единообразно на своей стороне.
    """
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, str)):
        return str(value)
    return None


def _parse_agency_client(item: Mapping[str, Any]) -> VkAgencyClient:
    """Один элемент `items[]` ответа `/agency/clients.json` → `VkAgencyClient`."""
    user = item.get("user")
    if not isinstance(user, dict):
        raise VkAgencyClientUnavailable("VK item has no user object")
    client_id = _as_str(user.get("id"))
    if client_id is None:
        raise VkAgencyClientUnavailable("VK item has no user.id")
    account = user.get("account")
    ad_account_id: str | None = None
    balance: str | None = None
    if isinstance(account, dict):
        ad_account_id = _as_str(account.get("id"))
        balance = _as_str(account.get("balance"))
    username = user.get("username")
    status = user.get("status")
    access_type = item.get("access_type")
    return VkAgencyClient(
        client_id=client_id,
        username=username if isinstance(username, str) else None,
        ad_account_id=ad_account_id,
        balance=balance,
        status=status if isinstance(status, str) else "unknown",
        access_type=access_type if isinstance(access_type, str) else "unknown",
    )


class VkApiAdapter(PlatformAdapter):
    """Адаптер прямого VK Ads API. `client` можно подменить (тесты/моки)."""

    def __init__(self, access_token: SecretStr, *, client: httpx.AsyncClient | None = None) -> None:
        self._token = access_token
        self._client = client
        self._geo = VkGeoResolver(self._fetch_regions)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token.get_secret_value()}"}

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        url = f"{BASE_URL}{path}"
        headers = {**self._headers(), **kwargs.pop("headers", {})}
        if self._client is not None:
            return await self._client.request(method, url, headers=headers, **kwargs)
        async with httpx.AsyncClient(timeout=30) as client:
            return await client.request(method, url, headers=headers, **kwargs)

    async def health_check(self) -> bool:
        """Read-only проверка: GET /user.json (scope read_user_info)."""
        try:
            response = await self._request("GET", "/user.json")
        except httpx.HTTPError:
            return False
        return response.status_code == 200

    async def create_cabinet(self, account_id: int, client_ref: str) -> str:
        """Создать клиентский кабинет (агентство) — тонкая обёртка контракта
        `PlatformAdapter` (возвращает только id строкой) поверх `create_agency_client`.

        `account_id` здесь не используется: в настоящем контракте VK у создания
        клиента агентства нет такого параметра — поле осталось только в сигнатуре
        интерфейса (см. отчёт задачи B1). Для дальнейшей работы (выпуск токена на
        кабинет через `agency_client_credentials`, баланс, id рекламного аккаунта)
        нужна вся структура — используйте `create_agency_client` напрямую, а не этот
        метод: `PlatformAdapter.create_cabinet` не может её вернуть без изменения
        абстрактного контракта (вне зоны этой задачи, см. отчёт).
        """
        client = await self.create_agency_client(client_name=client_ref)
        return client.client_id

    async def create_agency_client(
        self,
        *,
        client_name: str,
        client_info: str | None = None,
        additional_emails: Sequence[str] | None = None,
        user_id: str | None = None,
        username: str | None = None,
    ) -> VkAgencyClient:
        """Завести нового клиента агентства либо добавить уже существующего.

        `POST /api/v2/agency/clients.json` (документация VK,
        `ads.vk.ru/en/doc/api/resource/AgencyClients`; боевой хост API, как и везде в
        адаптере, — `ads.vk.com`). Тело вложенное: `access_type` обязателен
        (`full_access`); `user.id` ИЛИ `user.username` задаются, только когда
        добавляем УЖЕ существующего клиента — оставьте оба пустыми для нового.
        Контракт сверен с документацией, боевым вызовом ЕЩЁ НЕ проверен — это
        случится после подтверждения агентского аккаунта (план
        `docs/superpowers/plans/2026-08-25-agency-cabinets.md`, задача B1).

        Бросает `ValueError` (заданы одновременно `user_id` и `username`),
        `VkAgencyClientValidationError` (400), `VkAgencyClientForbidden` (403 —
        агентский статус не подтверждён либо нет права `create_clients`),
        `VkAgencyClientNotFound` (404 — неверная ссылка на существующего клиента)
        или `VkAgencyClientUnavailable` (сеть/5xx/битый ответ/пустой `items`).
        """
        if user_id and username:
            raise ValueError("provide at most one of user_id or username")

        additional_info: dict[str, str] = {"client_name": client_name}
        if client_info:
            additional_info["client_info"] = client_info
        user_body: dict[str, Any] = {"additional_info": additional_info}
        if additional_emails:
            user_body["additional_emails"] = list(additional_emails)
        if user_id:
            user_body["id"] = int(user_id)
        if username:
            user_body["username"] = username

        response = await self._request(
            "POST",
            AGENCY_CLIENTS_PATH,
            json={"access_type": AGENCY_ACCESS_FULL, "user": user_body},
        )
        _raise_for_agency_status(response)
        items = _agency_items(_agency_payload(response))
        if not items:
            raise VkAgencyClientUnavailable("VK response has no items")
        return _parse_agency_client(items[0])

    async def list_agency_clients(
        self,
        *,
        limit: int = AGENCY_CLIENTS_LIMIT_DEFAULT,
        offset: int = 0,
        user_id: str | None = None,
        username: str | None = None,
        status: str | None = None,
        query: str | None = None,
    ) -> VkAgencyClientPage:
        """Список клиентов агентства (`GET /api/v2/agency/clients.json`), с
        постраничностью и фильтрами.

        `limit` не может превышать лимит VK (`AGENCY_CLIENTS_LIMIT_MAX` = 50) —
        нарушение отклоняется `ValueError` ещё до сети. Карта отказов та же, что у
        `create_agency_client`.

        ⚠️ Задумывался как способ находить уже заведённых клиентов и не плодить
        дубли при повторе `create_agency_client` (B2), но этим методом сейчас
        НЕ пользуется никто: `services.agency_cabinets.create_client_cabinet`
        вызывает его нигде, а её единственная повторная попытка создания
        (`VkAgencyClientUnavailable` — сеть/5xx/битый ответ) неидемпотентна —
        если VK успел создать клиента, а ответ потерялся, повтор заведёт
        второго (ревью ветки §5). Дело не в лени: у новых клиентов при первом
        заведении нет `user_id`/`username` (они появляются только у уже
        существующего клиента), значит единственный связывающий признак —
        полнотекстовый `query` (`_q`) по `client_name`, а как VK на самом деле
        сопоставляет по нему (точное совпадение? подстрока?) невозможно
        проверить без боевого агентского доступа, которого пока нет
        (`vk_agency_confirmed` выключен). Подключать дедупликацию на этом
        методе вслепую — рискованнее, чем оставить как есть: ошибка сопоставления
        привяжет клиенту чужой существующий кабинет VK, а не просто оставит
        осиротевшего дубля на ручную уборку администратором (что уже
        предусмотрено — `AgencyTokenIssuanceFailedError`/
        `AgencyCabinetPersistError` несут `vk_client_id`/`vk_username`).
        Решение — сделать это на боевой проверке, когда будет с чем сверяться.
        """
        if not 1 <= limit <= AGENCY_CLIENTS_LIMIT_MAX:
            raise ValueError(f"limit must be between 1 and {AGENCY_CLIENTS_LIMIT_MAX}")
        if offset < 0:
            raise ValueError("offset must not be negative")

        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if user_id:
            params["_user__id"] = user_id
        if username:
            params["_user__username"] = username
        if status:
            params["_status"] = status
        if query:
            params["_q"] = query

        response = await self._request("GET", AGENCY_CLIENTS_PATH, params=params)
        _raise_for_agency_status(response)
        payload = _agency_payload(response)
        items = [_parse_agency_client(item) for item in _agency_items(payload)]
        return VkAgencyClientPage(
            items=items,
            count=int(payload.get("count", len(items))),
            limit=int(payload.get("limit", limit)),
            offset=int(payload.get("offset", offset)),
        )

    async def create_campaign(
        self, cabinet_id: str, goal: str, *, spec: CampaignSpec | None = None
    ) -> str:
        """Создать кампанию по контракту `PlatformAdapter`.

        Со спекой — обычная сборка через `create_campaign_from_spec`. Без спеки
        (контрактный вызов без данных брифа) отправляется минимальный план с одной
        кампанией-заглушкой: VK не принимает план с пустым `campaigns`, а объекта
        рекламы и таргетинга в этом вызове взять неоткуда.
        """
        if spec is not None:
            return await self.create_campaign_from_spec(cabinet_id, spec)
        name = f"plan-{cabinet_id}"
        logger.warning("Creating ad_plan %r without a spec: no targeting and no banner", name)
        body = _ad_plan_body(
            name=name,
            objective=goal,
            campaigns=[
                {
                    "name": name,
                    "package_id": surface_for(TargetType.COMMUNITY.value).package_id,
                    "autobidding_mode": AUTOBIDDING_MAX_GOALS,
                }
            ],
        )
        return await self._post_ad_plan(body)

    async def create_campaign_from_spec(
        self,
        cabinet_id: str,
        spec: CampaignSpec,
        *,
        creative_ref: str | None = None,
        title: str | None = None,
        body: str | None = None,
        budget_limit_day: float | None = None,
        activate: bool = False,
        prefer_video: bool = False,
    ) -> str:
        """Собрать кампанию целиком одним вложенным запросом (плюс загрузка медиа).

        Возвращает id ad_plan — именно он для ядра «идентификатор кампании»
        (по нему идут статус, остановка и статистика). Разложение спеки по
        уровням VK остаётся внутри адаптера: ядро об иерархии не знает.
        """
        ad_object = resolve_ad_object(spec.object_url, spec.object_kind)
        content: dict[str, str] = {}
        pattern = ad_object.surface.default_pattern
        if creative_ref and ad_object.surface.needs_creative:
            # Иконка обязательна в КАЖДОМ шаблоне VK, отдельного файла под неё нет —
            # готовим оба слота из одного присланного креатива. Медиа грузится ДО
            # плана: id нужен уже в теле вложенного banner.
            pattern = pattern_for_creative(
                ad_object.surface, creative_ref, prefer_video=prefer_video
            )
            for slot in (ICON_SLOT, pattern.media_slot):
                content[slot] = await self._upload_for_slot(
                    cabinet_id, creative_ref, slot, pattern.ratio
                )
        banner = _banner_body(
            ad_object,
            pattern=pattern,
            title=_fit(title or spec.name, TITLE_MAX_LEN),
            text=body or spec.name,
            content=content,
            url_id=await self.create_url_object(spec.object_url),
        )
        campaign = _campaign_body(
            spec,
            targetings=await self.build_targetings(spec),
            banners=[banner],
            budget_limit_day=budget_limit_day,
        )
        payload = _ad_plan_body(
            name=spec.name, objective=campaign_objective(spec), campaigns=[campaign]
        )
        plan_id = await self._post_ad_plan(payload)
        if not activate:
            # ⚠️ VK создаёт кампанию сразу в статусе `active` — не вызвать `launch()`
            # НЕДОСТАТОЧНО, деньги начнут списываться (боевая проверка 2026-07-27).
            # Поэтому гасим немедленно; по умолчанию создание неактивно.
            logger.info("Campaign %s created inactive: stopping right after creation", plan_id)
            await self.stop(plan_id)
        return plan_id

    async def _upload_for_slot(
        self, cabinet_id: str, creative_ref: str, slot: str, ratio: str = ""
    ) -> str:
        """Подогнать креатив под слот и загрузить.

        Видео-слот, а прислали картинку — собираем из неё короткий ролик (ffmpeg,
        `integrations/vk_video.py`): клиент, который не умеет монтировать, видео не
        пришлёт, а часть шаблонов принимает только его. Иконка при этом остаётся
        картинкой — она обязательна во всех шаблонах и всегда статична.
        """
        work_dir = Path(creative_ref).parent / "_vk"
        if slot.startswith("video_") and not is_video(creative_ref):
            video = await image_to_video(creative_ref, ratio or "1:1", work_dir)
            return await self.upload_creative(cabinet_id, str(video))

        target = slot_size(slot)
        if target is None or is_video(creative_ref):
            return await self.upload_creative(cabinet_id, creative_ref)
        prepared = await asyncio.to_thread(fit_to_slot, creative_ref, target, work_dir)
        return await self.upload_creative(cabinet_id, str(prepared))

    async def create_url_object(self, object_url: str) -> str:
        """Зарегистрировать ссылку объекта рекламы и вернуть id url-объекта.

        Объявление ссылается на объект рекламы только через этот id. VK сам
        разбирает короткий (vanity) адрес: `https://vk.ru/fin_dolm` принимается
        наравне с числовым `https://vk.com/id808632468` (боевая проверка 2026-07-27),
        поэтому вытаскивать числовой идентификатор на своей стороне не требуется.
        """
        response = await self._request("POST", "/urls.json", json={"url": object_url})
        response.raise_for_status()
        return str(response.json()["id"])

    async def _post_ad_plan(self, body: Mapping[str, Any]) -> str:
        """Отправить тело плана и вернуть id созданного ad_plan."""
        response = await self._request("POST", "/ad_plans.json", json=dict(body))
        response.raise_for_status()
        return str(response.json()["id"])

    async def build_targetings(self, spec: CampaignSpec) -> dict[str, Any]:
        """Собрать `targetings` кампании: гео (region id), возраст, пол, не-подписчики."""
        regions = await self._geo.resolve(spec.geo_raw)
        targetings: dict[str, Any] = {}
        if spec.age_list:
            targetings["age"] = {"age_list": [AGE_UNKNOWN, *spec.age_list], "expand": False}
        if spec.sex:
            targetings["sex"] = list(spec.sex)
        if resolve_ad_object(spec.object_url, spec.object_kind).is_community:
            targetings["group_members"] = NOT_GROUP_MEMBER
        targetings["geo"] = {"regions": regions}
        return targetings

    async def upload_creative(self, campaign_id: str, creative_ref: str) -> str:
        """Загрузить креатив (multipart) и вернуть content id.

        Эндпоинт зависит от типа файла: картинки принимает `/content/static.json`,
        видео — `/content/video.json`. Ролик, отправленный в статику, отвергается как
        `format_not_supported` (боевая проверка 2026-07-27).

        Имя файла обязано нести расширение: VK определяет формат по нему, а не по
        содержимому. С именем без расширения тот же PNG отвергается так же.
        """
        path = Path(creative_ref)
        content = await asyncio.to_thread(path.read_bytes)
        endpoint = VIDEO_UPLOAD_PATH if is_video(creative_ref) else STATIC_UPLOAD_PATH
        response = await self._request("POST", endpoint, files={"file": (path.name, content)})
        response.raise_for_status()
        return str(response.json()["id"])

    async def launch(self, campaign_id: str) -> None:
        """Перевести кампанию в активное состояние."""
        response = await self._request(
            "POST", f"/ad_plans/{campaign_id}.json", json={"status": STATUS_ACTIVE}
        )
        response.raise_for_status()

    async def stop(self, campaign_id: str) -> None:
        """Остановить кампанию и все её группы объявлений (в VK — статус `blocked`).

        Гасим оба уровня: остановки одного `ad_plan` недостаточно, деньги списываются
        по группам. Порядок именно такой — сперва план, потом группы, чтобы между
        запросами ничего не успело открутиться.
        """
        response = await self._request(
            "POST", f"/ad_plans/{campaign_id}.json", json={"status": STATUS_BLOCKED}
        )
        response.raise_for_status()
        for group_id in await self._campaign_ids(campaign_id):
            group = await self._request(
                "POST", f"/ad_groups/{group_id}.json", json={"status": STATUS_BLOCKED}
            )
            group.raise_for_status()

    async def delete_campaign(self, campaign_id: str) -> None:
        """Удалить кампанию в VK Ads API — перевод `ad_plan` в статус `deleted`.

        Подтверждено боевой проверкой 2026-08-23 (docs/VK_API_REFERENCE.md, раздел
        «Удаление кампании»): `POST /ad_plans/{id}.json` с телом `{"status":
        "deleted"}` отвечает `204 No Content` без тела. Статус `deleted` ОТДЕЛЬНЫЙ
        от `blocked` (его ставит `stop()`) — это не то же самое, что остановка.

        Это МЯГКОЕ удаление: кампания пропадает из обычной выдачи `/ad_plans.json`,
        но остаётся доступной при явном запросе по id — площадка её физически не
        стирает. Ответ пустой, поэтому `response.json()` здесь не вызываем (как и в
        `stop()`) — парсить нечего.
        """
        response = await self._request(
            "POST", f"/ad_plans/{campaign_id}.json", json={"status": STATUS_DELETED}
        )
        response.raise_for_status()

    async def _campaign_ids(self, campaign_id: str) -> list[str]:
        """Id групп объявлений плана.

        ⚠️ Читаем через `/ad_groups.json?_ad_plan_id=`, а НЕ через вложенное поле
        `campaigns` у `/ad_plans.json`: на чтение оно приходит пустым, хотя при
        создании обязательно (боевая проверка 2026-07-27). На пустом списке
        двухуровневая остановка молча вырождалась в остановку одного плана.
        """
        response = await self._request(
            "GET", "/ad_groups.json", params={"_ad_plan_id": campaign_id, "fields": "id"}
        )
        response.raise_for_status()
        items = response.json().get("items") or []
        return [str(entry["id"]) for entry in items if "id" in entry]

    async def get_status(self, campaign_id: str) -> str:
        """Текущий статус кампании (`active`/`blocked`/…); `unknown`, если поля нет.

        ⚠️ Читаем списком с явным `fields`, а НЕ через `/ad_plans/{id}.json`: одиночный
        эндпоинт отдаёт 200, но БЕЗ поля `status` (боевая проверка 2026-07-27). Статус
        оттуда всегда приходил `unknown`, и синхронизация статусов молча не работала.
        """
        response = await self._request(
            "GET", "/ad_plans.json", params={"_id": campaign_id, "fields": "id,status"}
        )
        response.raise_for_status()
        items = response.json().get("items") or []
        status = items[0].get("status") if items and isinstance(items[0], dict) else None
        return str(status) if status else "unknown"

    async def get_stats(self, campaign_id: str) -> dict[str, float]:
        """Снять сводную статистику кампании (base-метрики)."""
        response = await self._request(
            "GET",
            "/statistics/ad_plans/summary.json",
            params={"id": campaign_id, "metrics": "base"},
        )
        response.raise_for_status()
        return _parse_summary(response.json())

    async def _fetch_regions(self) -> list[dict[str, Any]]:
        """Справочник регионов для резолва гео (`GET /regions.json`).

        Заголовок `Accept-Language` переключает язык названий: без него VK отдаёт
        английские имена («Tver»), с `ru` — русские («Тверь»), совпадающие с тем,
        как гео пишут в брифе. Query-параметры `lang`/`locale` при этом
        игнорируются (проверено живьём 2026-07-25).
        """
        response = await self._request(
            "GET", "/regions.json", headers={"Accept-Language": REGIONS_LANGUAGE}
        )
        response.raise_for_status()
        payload = response.json()
        items = payload.get("items", []) if isinstance(payload, dict) else payload
        return [item for item in items if isinstance(item, dict)]


def _ad_plan_body(
    *, name: str, objective: str, campaigns: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Тело `POST /ad_plans.json`: план целиком, вместе с вложенными кампаниями.

    `budget_limit_day` на этом уровне НЕ ставится — у живого плана он `null`,
    дневной лимит живёт внутри `campaigns[]`.
    """
    return {
        "name": name,
        "objective": objective,
        "date_start": _today(),
        "campaigns": [dict(campaign) for campaign in campaigns],
    }


def _campaign_body(
    spec: CampaignSpec,
    *,
    targetings: Mapping[str, Any],
    banners: Sequence[Mapping[str, Any]],
    budget_limit_day: float | None = None,
) -> dict[str, Any]:
    """Тело вложенной кампании (в брифе — «группа объявлений»): пакет, таргетинг, бюджет.

    Пересчёт бюджета брифа в дневной лимит — ответственность сервиса запуска;
    сюда он приходит готовым значением, иначе ключ не отправляется вовсе.
    """
    ad_object = resolve_ad_object(spec.object_url, spec.object_kind)
    body: dict[str, Any] = {
        "name": spec.name,
        "package_id": ad_object.package_id,
        "autobidding_mode": ad_object.surface.autobidding,
        "targetings": dict(targetings),
        "banners": [dict(banner) for banner in banners],
    }
    if budget_limit_day is not None:
        body["budget_limit_day"] = float(budget_limit_day)
    return body


def _banner_body(
    ad_object: AdObject,
    *,
    pattern: Pattern | None,
    title: str,
    text: str,
    content: Mapping[str, str],
    url_id: str,
    cta: str | None = None,
    about_company: str | None = None,
) -> dict[str, Any]:
    """Тело вложенного объявления: медиа по слотам, тексты и ссылка на объект рекламы.

    Имена слотов берутся из шаблона, подобранного под креатив: у каждой площадки свои
    кнопка и лимиты. Крайние случаи, ради которых слоты и живут в справочнике:
    у каналов VK и MAX текст — `text_90`, а не 2000 символов; у Дзена кнопки нет
    вовсе, заголовок ограничен 25 символами, требуется имя канала и ссылка уходит
    в слот `dzen_publication`, а не в `primary`.

    Слот ссылки принимает ТОЛЬКО `id` заранее созданного url-объекта: поля `url` и
    `url_object_type` в запросе доступны лишь на чтение (`read_only_field`), а без `id`
    приходит `required / Empty value` (боевая проверка 2026-07-27).
    """
    if pattern is None:
        # Продвижение готового поста: объявлением служит сам пост, содержимого нет.
        return {"urls": {ad_object.surface.url_slot: {"id": int(url_id)}}}

    textblocks: dict[str, dict[str, str]] = {
        pattern.title_slot: {"text": _fit(title, text_limit(pattern.title_slot))},
        pattern.text_slot: {"text": _fit(text, text_limit(pattern.text_slot))},
    }
    if pattern.cta_slot:
        textblocks[pattern.cta_slot] = {"text": cta or ad_object.surface.default_cta}
    for slot in pattern.extra_slots:
        # Короткие слоты (имя канала, подзаголовок) заполняем заголовком, длинные —
        # текстом объявления: так подпись остаётся осмысленной, а не обрубком.
        limit = text_limit(slot)
        source = title if limit <= text_limit(SLOT_TITLE) else text
        textblocks[slot] = {"text": _fit(source, limit)}
    if about_company:
        # Юр. данные рекламодателя. Слот необязательный во всех шаблонах площадок.
        textblocks[SLOT_ABOUT_COMPANY] = {
            "text": _fit(about_company, text_limit(SLOT_ABOUT_COMPANY))
        }

    return {
        "content": _content_body(content),
        "textblocks": textblocks,
        "urls": {ad_object.surface.url_slot: {"id": int(url_id)}},
    }


def _content_body(content: Mapping[str, str]) -> dict[str, dict[str, int]]:
    """Перевести «слот → id загруженного медиа» в тело banner.content."""
    body: dict[str, dict[str, int]] = {}
    for slot, content_id in content.items():
        if slot not in CONTENT_SLOTS:
            raise ValueError(f"Unknown content slot: {slot}")
        body[slot] = {"id": int(content_id)}
    return body


def _as_float(value: Any) -> float | None:
    """VK отдаёт часть чисел строками (`spent`, `cpc`) — приводим явно."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _base_block(payload: dict[str, Any]) -> dict[str, Any]:
    """Достать блок base-метрик: и из обёртки `items`, и из плоского `total`."""
    items = payload.get("items") or []
    total = items[0].get("total") if items and isinstance(items[0], dict) else payload.get("total")
    base = (total or {}).get("base") if isinstance(total, dict) else None
    return base if isinstance(base, dict) else {}


def _result_value(base: dict[str, Any]) -> float | None:
    """Результат по цели (подписки): он в `base.vk.result`, а не в верхнем `goals`.

    Верхнеуровневый `goals` у живой кампании равен 0 — брать его нельзя.
    """
    vk_block = base.get("vk")
    if isinstance(vk_block, dict):
        for key in ("result", "goals"):
            value = _as_float(vk_block.get(key))
            if value is not None:
                return value
    return _as_float(base.get("goals"))


def _parse_summary(payload: dict[str, Any]) -> dict[str, float]:
    """Достать base-метрики из ответа статистики VK (показы/клики/расход/CTR/результат)."""
    base = _base_block(payload)
    if not base:
        return {}
    stats: dict[str, float] = {}
    for key in _BASE_METRICS:
        value = _as_float(base.get(key))
        if value is not None:
            stats[key] = value
    result = _result_value(base)
    if result is not None:
        # Ключ `goals` — контракт с `services/stats.py` (результат по цели).
        stats["goals"] = result
    return stats
