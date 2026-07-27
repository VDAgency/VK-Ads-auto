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
