"""Резолв гео из брифа («Москва», «вся Россия») в region id VK Ads.

Справочник `GET /api/v2/regions.json` отдаёт ~5.5 тыс. регионов. Язык названий
переключается **заголовком** `Accept-Language` (query-параметры `lang`/`locale`
игнорируются): с `ru` приходят «Москва», «Тверь», «Московская область» — ровно в том
виде, в каком гео пишут в брифе. Поэтому основной путь резолва — прямой поиск по
русскому имени, и он работает для любого населённого пункта справочника, а не только
для заранее перечисленных.

Словарь ниже — не список поддерживаемых городов, а перевод разговорных форм
(«СПб», «Питер», «Подмосковье») в официальные названия справочника.

Что не опознали — пишем в лог и падаем на фолбэк «Россия» (id 188), чтобы кампания не
осталась без гео. HTTP-вызов справочника инжектится извне (`VkApiAdapter` знает
базовый URL, токен и заголовок языка), модуль остаётся без сетевых зависимостей.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

# Фолбэк: вся Россия (подтверждено живым запросом справочника).
RUSSIA_REGION_ID = 188

# Опорные значения справочника (подтверждены живьём): Россия=188, Москва=5506,
# Санкт-Петербург=5560, Тверь=5567 — то есть обычные города резолвятся по имени сами.
# Здесь только разговорные формы, которых в справочнике VK нет.
_ALIASES: dict[str, str] = {
    "вся россия": "россия",
    "по всей россии": "россия",
    "рф": "россия",
    "россия целиком": "россия",
    "спб": "санкт-петербург",
    "санкт петербург": "санкт-петербург",
    "петербург": "санкт-петербург",
    "питер": "санкт-петербург",
    "мск": "москва",
    "подмосковье": "московская область",
    "ленобласть": "ленинградская область",
    "екб": "екатеринбург",
    "нижний": "нижний новгород",
    "ростов": "ростов-на-дону",
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
            # Имена нормализуем так же, как гео из брифа (ё→е, регистр, пробелы),
            # иначе «Королёв» из справочника не сойдётся с «Королев» из брифа.
            name = _normalize(str(region.get("name", "")))
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
            reference_name = _ALIASES.get(name, name)
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
