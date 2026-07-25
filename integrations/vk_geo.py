"""Резолв гео из брифа («Москва», «вся Россия») в region id VK Ads.

Справочник `GET /api/v2/regions.json` отдаёт ~5.5 тыс. регионов и **только английские**
имена — параметры `lang`/`locale` игнорируются (docs/VK_API_REFERENCE.md, разведка
2026-07-25). Поэтому резолв двухступенчатый: собственный RU→EN словарь частых
формулировок брифа, затем поиск по имени в справочнике. Что не опознали — пишем в лог
и падаем на фолбэк «Россия» (id 188), чтобы кампания не осталась без гео.

HTTP-вызов справочника инжектится извне (`VkApiAdapter` знает базовый URL и токен),
модуль остаётся без сетевых зависимостей и легко тестируется.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# Фолбэк: вся Россия (подтверждено живым запросом справочника).
RUSSIA_REGION_ID = 188

# Опорные значения справочника (подтверждены живьём): Moscow=5506, Sankt-Peterburg=5560.
_RU_TO_EN: dict[str, str] = {
    "россия": "Russia",
    "вся россия": "Russia",
    "по всей россии": "Russia",
    "рф": "Russia",
    "москва": "Moscow",
    "московская область": "Moscow Oblast",
    "подмосковье": "Moscow Oblast",
    "санкт-петербург": "Sankt-Peterburg",
    "санкт петербург": "Sankt-Peterburg",
    "спб": "Sankt-Peterburg",
    "питер": "Sankt-Peterburg",
    "ленинградская область": "Leningrad Oblast",
    "новосибирск": "Novosibirsk",
    "екатеринбург": "Ekaterinburg",
    "казань": "Kazan",
    "нижний новгород": "Nizhniy Novgorod",
    "челябинск": "Chelyabinsk",
    "самара": "Samara",
    "омск": "Omsk",
    "ростов-на-дону": "Rostov-na-Donu",
    "уфа": "Ufa",
    "красноярск": "Krasnoyarsk",
    "воронеж": "Voronezh",
    "пермь": "Perm",
    "волгоград": "Volgograd",
    "краснодар": "Krasnodar",
    "сочи": "Sochi",
    "тюмень": "Tyumen",
    "саратов": "Saratov",
    "тольятти": "Tolyatti",
    "ижевск": "Izhevsk",
    "барнаул": "Barnaul",
    "ульяновск": "Ulyanovsk",
    "иркутск": "Irkutsk",
    "хабаровск": "Khabarovsk",
    "владивосток": "Vladivostok",
    "ярославль": "Yaroslavl",
    "томск": "Tomsk",
    "оренбург": "Orenburg",
    "кемерово": "Kemerovo",
    "рязань": "Ryazan",
    "астрахань": "Astrakhan",
    "пенза": "Penza",
    "липецк": "Lipetsk",
    "тула": "Tula",
    "киров": "Kirov",
    "калининград": "Kaliningrad",
    "ставрополь": "Stavropol",
    "белгород": "Belgorod",
}

# Разделители перечисления гео в брифе: запятая/точка с запятой/слэш и союз «и».
_SPLIT_RE = re.compile(r"[,;/|]|\s+и\s+")
# Служебные префиксы («г. Москва», «город Москва»).
_PREFIX_RE = re.compile(r"^(?:г\.?|город|обл\.?|область)\s+")
_TRIM_CHARS = " .,:;\"'«»()"

# Асинхронный поставщик справочника регионов (список сырых записей VK).
RegionsFetcher = Callable[[], Awaitable[list[dict[str, Any]]]]


def _normalize(value: str) -> str:
    """Привести текст гео к сравнимому виду: нижний регистр, ё→е, схлопнутые пробелы."""
    text = value.replace("ё", "е").replace("Ё", "Е").lower()
    text = re.sub(r"\s+", " ", text).strip(_TRIM_CHARS)
    return _PREFIX_RE.sub("", text).strip(_TRIM_CHARS)


def split_geo(geo_raw: str) -> list[str]:
    """Разбить строку гео из брифа на отдельные нормализованные названия."""
    normalized = _normalize(geo_raw)
    if not normalized:
        return []
    parts = (_normalize(part) for part in _SPLIT_RE.split(normalized))
    return [part for part in parts if part]


class VkGeoResolver:
    """Переводит гео-текст брифа в `targetings.geo.regions`. Справочник кэшируется."""

    def __init__(self, fetch_regions: RegionsFetcher) -> None:
        self._fetch_regions = fetch_regions
        self._index: dict[str, int] | None = None

    async def _name_index(self) -> dict[str, int]:
        """Индекс «имя региона (lower) → id». Загружается один раз на экземпляр."""
        if self._index is not None:
            return self._index
        try:
            regions = await self._fetch_regions()
        except Exception as exc:  # сеть/формат — не роняем запуск, уходим в фолбэк
            logger.warning("VK regions reference unavailable: %s", exc)
            return {}
        index: dict[str, int] = {}
        for region in regions:
            name = str(region.get("name", "")).strip().lower()
            region_id = region.get("id")
            if name and isinstance(region_id, int) and name not in index:
                index[name] = region_id
        self._index = index
        return index

    async def resolve(self, geo_raw: str) -> list[int]:
        """Вернуть region id для гео из брифа; при неудаче — [Россия] с предупреждением."""
        names = split_geo(geo_raw)
        if not names:
            logger.warning("Empty geo in brief, falling back to Russia (%s)", RUSSIA_REGION_ID)
            return [RUSSIA_REGION_ID]

        index = await self._name_index()
        resolved: list[int] = []
        for name in names:
            reference_name = _RU_TO_EN.get(name, name).lower()
            region_id = index.get(reference_name)
            if region_id is None:
                logger.warning("Unknown geo %r in VK regions reference, skipped", name)
                continue
            if region_id not in resolved:
                resolved.append(region_id)

        if not resolved:
            logger.warning(
                "Geo %r not resolved, falling back to Russia (%s)", geo_raw, RUSSIA_REGION_ID
            )
            return [RUSSIA_REGION_ID]
        return resolved
