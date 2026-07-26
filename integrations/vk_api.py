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
from integrations.vk_geo import VkGeoResolver

logger = logging.getLogger(__name__)

BASE_URL = "https://ads.vk.com/api/v2"

# Пакеты и цели под «подписчики» (живой справочник пакетов, 2026-07-25).
# 3127 — это «написать сообщение», НЕ подписчики: не использовать.
PACKAGE_COMMUNITY = 3122
PACKAGE_PROFILE = 3268
OBJECTIVE_COMMUNITY = "socialengagement"
OBJECTIVE_PROFILE = "socialengagement_profile"

# Тип объекта рекламы в `urls.primary`. `vk_group` подтверждён эталоном;
# `vk_user` для личной страницы — VERIFY при первой боевой мутации.
URL_OBJECT_COMMUNITY = "vk_group"
URL_OBJECT_PROFILE = "vk_user"

# Автобиддинг под цель «подписчики» (у эталонной группы max_goals).
AUTOBIDDING_MAX_GOALS = "max_goals"
# Остановка кампании в VK — перевод статуса в `blocked`.
STATUS_ACTIVE = "active"
STATUS_BLOCKED = "blocked"

# Слоты пакета 3122 (для 3268 набор не подтверждён — VERIFY).
SLOT_TITLE = "title_40_vkads"
SLOT_TEXT = "text_2000"
SLOT_CTA = "cta_community_vk"
SLOT_ABOUT_COMPANY = "about_company_115"
IMAGE_CONTENT_SLOT = "icon_256x256"
VIDEO_CONTENT_SLOT = "video_portrait_9_16_180s"
CONTENT_SLOTS = frozenset({IMAGE_CONTENT_SLOT, VIDEO_CONTENT_SLOT})
DEFAULT_CTA = "signUp"

# Ограничения слотов текстов пакета (title_40_vkads / text_2000).
TITLE_MAX_LEN = 40
TEXT_MAX_LEN = 2000

_VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm"})

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
    """Объект рекламы (сообщество или личная страница) и вытекающие параметры VK."""

    url: str
    url_object_type: str
    url_object_id: str | None
    package_id: int
    objective: str

    @property
    def is_community(self) -> bool:
        return self.url_object_type == URL_OBJECT_COMMUNITY


def _object_slug(object_url: str) -> str:
    """Первый сегмент пути ссылки: `club228817082`, `id777`, короткий адрес."""
    raw = object_url.strip()
    if "//" not in raw:
        raw = f"https://{raw}"
    segments = [segment for segment in urlsplit(raw).path.split("/") if segment]
    return segments[0].lower() if segments else ""


def _profile_object(url: str, object_id: str | None) -> AdObject:
    """Личная страница: пакет 3268, objective `socialengagement_profile`."""
    return AdObject(
        url=url,
        url_object_type=URL_OBJECT_PROFILE,
        url_object_id=object_id,
        package_id=PACKAGE_PROFILE,
        objective=OBJECTIVE_PROFILE,
    )


def _community_object(url: str, object_id: str | None) -> AdObject:
    """Сообщество: пакет 3122, objective `socialengagement`."""
    return AdObject(
        url=url,
        url_object_type=URL_OBJECT_COMMUNITY,
        url_object_id=object_id,
        package_id=PACKAGE_COMMUNITY,
        objective=OBJECTIVE_COMMUNITY,
    )


def resolve_ad_object(object_url: str, kind: str = "") -> AdObject:
    """Определить тип объекта рекламы и подобрать package_id/objective.

    Числовой адрес — факт и решает сам: `vk.com/club…`, `public…`, `event…` —
    сообщество (пакет 3122); `vk.com/id…` — личная страница (пакет 3268).

    Короткий адрес (`vk.ru/fin_dolm`) человека от сообщества не отличает. Для него
    авторитетна подсказка `kind` из брифа (`personal_page`/`community`), где тип
    объекта указан явно. Без подсказки остаётся прежняя эвристика «сообщество» —
    самый частый случай брифа; числовой id тогда неизвестен и в тело уйдёт только `url`.
    """
    slug = _object_slug(object_url)
    url = object_url.strip()

    profile = _PROFILE_RE.match(slug)
    if profile:
        return _profile_object(url, profile.group(1))

    community = _COMMUNITY_RE.match(slug)
    if community:
        return _community_object(url, community.group(1))

    # Дальше — короткий адрес: числового id нет, тип берём из брифа.
    if kind == TargetType.PERSONAL_PAGE.value:
        logger.warning("Vanity VK url %r: personal page per brief, numeric id unknown", url)
        return _profile_object(url, None)
    if kind != TargetType.COMMUNITY.value:
        logger.warning("Vanity VK url %r without brief hint: assuming community", url)
    return _community_object(url, None)


def campaign_objective(spec: CampaignSpec) -> str:
    """Objective для ad_plan: из спеки, но для личной страницы — profile-вариант.

    `services/mapping.py` пока отдаёт `socialengagement` для обоих вариантов брифа,
    а у личной страницы цель другая — правим здесь, где известен объект рекламы.
    """
    resolved = resolve_ad_object(spec.object_url, spec.object_kind).objective
    if resolved != spec.objective:
        logger.warning(
            "Objective %r from spec replaced with %r for personal page", spec.objective, resolved
        )
    return resolved


def content_slot(creative_ref: str) -> str:
    """Слот медиа объявления по расширению файла: видео → видеослот, иначе картинка."""
    return (
        VIDEO_CONTENT_SLOT
        if Path(creative_ref).suffix.lower() in _VIDEO_SUFFIXES
        else IMAGE_CONTENT_SLOT
    )


def _fit(text: str, limit: int) -> str:
    """Обрезать текст под лимит слота VK — площадка длиннее не принимает."""
    return text[:limit]


def _today() -> str:
    """Дата старта кампании в формате VK (`YYYY-MM-DD`)."""
    return date.today().isoformat()


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
        """Создать клиентский кабинет (агентство). Тело — см. справку (verify)."""
        response = await self._request("POST", "/agency/clients.json", json={"name": client_ref})
        response.raise_for_status()
        return str(response.json()["id"])

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
                    "package_id": PACKAGE_COMMUNITY,
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
    ) -> str:
        """Собрать кампанию целиком одним вложенным запросом (плюс загрузка медиа).

        Возвращает id ad_plan — именно он для ядра «идентификатор кампании»
        (по нему идут статус, остановка и статистика). Разложение спеки по
        уровням VK остаётся внутри адаптера: ядро об иерархии не знает.
        """
        content: dict[str, str] = {}
        if creative_ref:
            # Медиа грузится ДО плана: id нужен уже в теле вложенного banner.
            content[content_slot(creative_ref)] = await self.upload_creative(
                cabinet_id, creative_ref
            )
        banner = _banner_body(
            spec,
            title=_fit(title or spec.name, TITLE_MAX_LEN),
            text=_fit(body or spec.name, TEXT_MAX_LEN),
            content=content,
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
        return await self._post_ad_plan(payload)

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
        """Загрузить статичный креатив (multipart) и вернуть content id."""
        content = await asyncio.to_thread(Path(creative_ref).read_bytes)
        files = {"file": ("creative", content)}
        response = await self._request("POST", "/content/static.json", files=files)
        response.raise_for_status()
        return str(response.json()["id"])

    async def launch(self, campaign_id: str) -> None:
        """Перевести кампанию в активное состояние."""
        response = await self._request(
            "POST", f"/ad_plans/{campaign_id}.json", json={"status": STATUS_ACTIVE}
        )
        response.raise_for_status()

    async def stop(self, campaign_id: str) -> None:
        """Остановить кампанию (в VK — статус `blocked`)."""
        response = await self._request(
            "POST", f"/ad_plans/{campaign_id}.json", json={"status": STATUS_BLOCKED}
        )
        response.raise_for_status()

    async def get_status(self, campaign_id: str) -> str:
        """Текущий статус кампании (`active`/`blocked`/…); `unknown`, если поля нет."""
        response = await self._request("GET", f"/ad_plans/{campaign_id}.json")
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status") if isinstance(payload, dict) else None
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
        "autobidding_mode": AUTOBIDDING_MAX_GOALS,
        "targetings": dict(targetings),
        "banners": [dict(banner) for banner in banners],
    }
    if budget_limit_day is not None:
        body["budget_limit_day"] = float(budget_limit_day)
    return body


def _banner_body(
    spec: CampaignSpec,
    *,
    title: str,
    text: str,
    content: Mapping[str, str],
    cta: str = DEFAULT_CTA,
    about_company: str | None = None,
) -> dict[str, Any]:
    """Тело вложенного объявления: медиа по слотам, тексты и ссылка на объект рекламы."""
    ad_object = resolve_ad_object(spec.object_url, spec.object_kind)
    textblocks: dict[str, dict[str, str]] = {
        SLOT_TITLE: {"text": title},
        SLOT_TEXT: {"text": text},
        SLOT_CTA: {"text": cta},
    }
    if about_company:
        # Юр. данные рекламодателя (347-ФЗ). VERIFY: обязательность при создании.
        textblocks[SLOT_ABOUT_COMPANY] = {"text": about_company}

    primary: dict[str, str] = {
        "url": ad_object.url,
        "url_object_type": ad_object.url_object_type,
    }
    if ad_object.url_object_id is not None:
        primary["url_object_id"] = ad_object.url_object_id

    return {
        "content": _content_body(content),
        "textblocks": textblocks,
        "urls": {"primary": primary},
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
