"""Справочник площадок цели «подписчики»: куда VK Реклама умеет вести подписку.

Подписка — не одна цель, а семейство. Живой `/packages.json` (174 пакета, 29 целей,
снято 2026-07-27) даёт семь площадок, на которые человек может подписаться: сообщество
и личная страница ВК, рассылка через мини-приложение, канал VK, канал MAX, сообщество и
профиль в Одноклассниках. Все они идут через один и тот же кабинет и один и тот же API —
различаются пакетом, целью и набором допустимых шаблонов объявления.

## Откуда взяты шаблоны

Узнать шаблоны пакета из справочника нельзя: `/packages/{id}.json` → 404, фильтра по
пакету у `/banner_patterns.json` нет, `banner_format_id` у всех подписных пакетов = 0.
Зато VK сам их перечисляет: если отправить объявление без содержимого, ответ 400 несёт
`patterns.arguments.patterns` — список разрешённых пакетом шаблонов. Раскрытые через
`/banner_patterns.json`, они и легли в таблицы ниже (снято 2026-07-27).

Повторить разведку для новой площадки: `POST /ad_plans.json` с нужным `package_id` и
пустым баннером, затем сопоставить id из ошибки со справочником шаблонов.

## Как устроен шаблон

Объявление обязано совпасть с одним из разрешённых шаблонов, иначе приходит
`bad_value: At least one pattern must be in package's settings`. Шаблон — это всегда
иконка 256×256 плюс ровно один основной медиа-слот, плюс заголовок, текст и кнопка.
Id шаблона в запросе НЕ передаётся: VK подбирает его сам по набору присланных слотов.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# --- Слоты, общие для всех площадок ------------------------------------------------
ICON_SLOT = "icon_256x256"
ICON_SIZE = (256, 256)

SLOT_TITLE = "title_40_vkads"
SLOT_ABOUT_COMPANY = "about_company_115"

# Точные размеры картиночных слотов. VK принимает загрузку любого размера, но при сборке
# объявления сверяет его со слотом (`bad_width`), поэтому кадр приводим сами.
IMAGE_SLOT_SIZES: dict[str, tuple[int, int]] = {
    ICON_SLOT: ICON_SIZE,
    "image_600x600": (600, 600),
    "image_1080x607": (1080, 607),
    "image_607x1080": (607, 1080),
    "image_1080x1350": (1080, 1350),
    "image_4_5": (1080, 1350),
}

# Лимиты текстовых слотов: длиннее площадка не принимает.
TEXT_SLOT_LIMITS: dict[str, int] = {
    SLOT_TITLE: 40,
    "title_30_additional": 30,
    "text_2000": 2000,
    "text_90": 90,
    SLOT_ABOUT_COMPANY: 115,
}

# Соотношения сторон, которые различает VK.
RATIO_SQUARE = "1:1"
RATIO_LANDSCAPE = "16:9"
RATIO_PORTRAIT = "9:16"
RATIO_PORTRAIT_4_5 = "4:5"

RATIO_VALUES: dict[str, float] = {
    RATIO_SQUARE: 1.0,
    RATIO_LANDSCAPE: 16 / 9,
    RATIO_PORTRAIT: 9 / 16,
    RATIO_PORTRAIT_4_5: 4 / 5,
}

# Имена кнопок и текстовых слотов различаются по площадкам — из-за них объявление и
# не совпадало с шаблоном.
CTA_COMMUNITY = "cta_community_vk"
CTA_PROFILE = "cta_profile_vk"
CTA_MINIAPP = "cta_miniapp_vk"
CTA_SITES = "cta_sites_full"

TEXT_LONG = "text_2000"
TEXT_SHORT = "text_90"

_DURATION_RE = re.compile(r"_(\d+)s$")


def _slot_ratio(slot: str) -> str:
    """Соотношение сторон, зашитое в имя слота.

    Имена у VK самоописательные (`video_portrait_9_16_180s`, `image_1080x607`), поэтому
    разбираем их, а не дублируем те же данные третьим столбцом в каждой строке таблицы.
    """
    if "square" in slot:
        return RATIO_SQUARE
    if "9_16" in slot:
        return RATIO_PORTRAIT
    if "4_5" in slot:
        return RATIO_PORTRAIT_4_5
    if "landscape" in slot:
        return RATIO_LANDSCAPE

    size = IMAGE_SLOT_SIZES.get(slot)
    if size is None:
        raise ValueError(f"Не удалось определить соотношение сторон слота {slot!r}")
    width, height = size
    return min(RATIO_VALUES, key=lambda name: abs(RATIO_VALUES[name] - width / height))


@dataclass(frozen=True)
class Pattern:
    """Один разрешённый шаблон объявления: основной медиа-слот и его текстовая обвязка."""

    pattern_id: int
    media_slot: str
    cta_slot: str
    text_slot: str

    @property
    def is_video(self) -> bool:
        return self.media_slot.startswith("video_")

    @property
    def ratio(self) -> str:
        return _slot_ratio(self.media_slot)

    @property
    def max_seconds(self) -> int | None:
        """Предел длительности ролика из имени слота; у картинок его нет."""
        found = _DURATION_RE.search(self.media_slot)
        return int(found.group(1)) if found else None

    @property
    def is_portrait(self) -> bool:
        return self.ratio in (RATIO_PORTRAIT, RATIO_PORTRAIT_4_5)


@dataclass(frozen=True)
class Surface:
    """Площадка подписки: пакет VK, цель кампании и допустимые шаблоны объявления."""

    kind: str
    title: str
    hint: str
    package_id: int
    objective: str
    default_cta: str
    patterns: tuple[Pattern, ...]
    verified: bool = False

    @property
    def ratios(self) -> tuple[str, ...]:
        """Соотношения сторон, которые площадка вообще принимает."""
        return tuple(sorted({pattern.ratio for pattern in self.patterns}))

    def patterns_for(self, *, is_video: bool) -> tuple[Pattern, ...]:
        return tuple(pattern for pattern in self.patterns if pattern.is_video is is_video)

    @property
    def default_pattern(self) -> Pattern:
        """Шаблон, когда креатива ещё нет: нужен ради имён слотов кнопки и текста.

        Первым в каждом наборе идёт квадратная картинка — самый нейтральный вариант.
        """
        return self.patterns[0]


def _patterns(cta: str, text: str, table: dict[int, str]) -> tuple[Pattern, ...]:
    """Собрать шаблоны площадки из таблицы «id шаблона → основной медиа-слот»."""
    return tuple(Pattern(pattern_id, slot, cta, text) for pattern_id, slot in table.items())


# --- ВКонтакте: сообщество (пакет 3122) --------------------------------------------
VK_COMMUNITY = Surface(
    kind="community",
    title="Сообщество ВКонтакте",
    hint="ссылка на сообщество: vk.com/club… или короткий адрес",
    package_id=3122,
    objective="socialengagement",
    default_cta="signUp",
    verified=True,
    patterns=_patterns(
        CTA_COMMUNITY,
        TEXT_LONG,
        {
            529: "image_600x600",
            400: "image_1080x607",
            525: "image_607x1080",
            339: "image_4_5",
            530: "video_square_300s",
            401: "video_landscape_300s",
            527: "video_portrait_9_16_180s",
            145: "video_portrait_9_16_30s",
            338: "video_portrait_4_5_180s",
            150: "video_portrait_4_5_30s",
        },
    ),
)

# --- ВКонтакте: личная страница (пакет 3268) ---------------------------------------
# Квадрат и горизонталь — «профильные» шаблоны с кнопкой `cta_profile_vk`; вертикаль
# доступна только через шаблоны сообщества, с их кнопкой. Картинки 9:16 у страницы нет
# вовсе (шаблон 525 в пакет не входит) — вертикальный кадр уходит в 4:5.
VK_PERSONAL = Surface(
    kind="personal_page",
    title="Личная страница ВКонтакте",
    hint="ссылка на страницу: vk.com/id… или короткий адрес",
    package_id=3268,
    objective="socialengagement_profile",
    default_cta="signUp",
    verified=True,
    patterns=(
        *_patterns(
            CTA_PROFILE,
            TEXT_LONG,
            {
                535: "image_600x600",
                519: "image_1080x607",
                534: "video_square_300s",
                520: "video_landscape_300s",
            },
        ),
        *_patterns(
            CTA_COMMUNITY,
            TEXT_LONG,
            {
                339: "image_4_5",
                527: "video_portrait_9_16_180s",
                145: "video_portrait_9_16_30s",
                338: "video_portrait_4_5_180s",
                150: "video_portrait_4_5_30s",
            },
        ),
    ),
)

# --- ВКонтакте: рассылка через мини-приложение (пакет 3210) -------------------------
# Самая ходовая площадка у оператора: 30 групп из живых кампаний кабинета. Объект
# рекламы — страница мини-приложения (`vk_miniapp_page`), например ссылка Senler.
VK_NEWSLETTER = Surface(
    kind="newsletter",
    title="Рассылка ВКонтакте",
    hint="ссылка на мини-приложение рассылки, например vk.com/app…",
    package_id=3210,
    objective="vk_miniapps",
    default_cta="visitSite",
    patterns=_patterns(
        CTA_MINIAPP,
        TEXT_LONG,
        {
            532: "image_600x600",
            489: "image_1080x607",
            75: "image_607x1080",
            83: "image_1080x1350",
            533: "video_square_300s",
            492: "video_landscape_16_9_300s",
            521: "video_portrait_9_16_180s",
            566: "video_portrait_9_16_30s",
            522: "video_portrait_4_5_180s",
            575: "video_portrait_4_5_30s",
        },
    ),
)

# --- Каналы VK и MAX (пакеты 4606 и 4686) ------------------------------------------
# У обоих один и тот же набор шаблонов «для сайтов»: кнопка `cta_sites_full` и КОРОТКИЙ
# текст `text_90` вместо привычного `text_2000` — это единственные площадки с таким
# ограничением, и текст объявления придётся резать до 90 символов.
_CHANNEL_PATTERNS = _patterns(
    CTA_SITES,
    TEXT_SHORT,
    {
        514: "image_600x600",
        513: "image_1080x607",
        515: "image_1080x1350",
        510: "video_square_180s",
        512: "video_landscape_180s",
        509: "video_portrait_9_16_180s",
        321: "video_portrait_9_16_30s",
        511: "video_portrait_4_5_180s",
        323: "video_portrait_4_5_30s",
    },
)

VK_CHANNEL = Surface(
    kind="vk_channel",
    title="Канал ВКонтакте",
    hint="ссылка на канал ВКонтакте",
    package_id=4606,
    objective="vk_channel",
    default_cta="visitSite",
    patterns=_CHANNEL_PATTERNS,
)

MAX_CHANNEL = Surface(
    kind="max_channel",
    title="Канал MAX",
    hint="ссылка на канал MAX",
    package_id=4686,
    objective="max_channel",
    default_cta="visitSite",
    patterns=_CHANNEL_PATTERNS,
)

# --- Одноклассники (пакеты 3466 и 3845) --------------------------------------------
# Идут через тот же кабинет VK Рекламы — отдельная интеграция с ОК не нужна.
# Вертикали нет ни у сообщества, ни у профиля: только квадрат и горизонталь.
OK_COMMUNITY = Surface(
    kind="ok_community",
    title="Сообщество в Одноклассниках",
    hint="ссылка на группу: ok.ru/group/…",
    package_id=3466,
    objective="odkl",
    default_cta="signUp",
    patterns=_patterns(
        CTA_COMMUNITY,
        TEXT_LONG,
        {
            555: "image_600x600",
            553: "image_1080x607",
            556: "video_square_300s",
            554: "video_landscape_300s",
        },
    ),
)

OK_PROFILE = Surface(
    kind="ok_profile",
    title="Профиль в Одноклассниках",
    hint="ссылка на профиль: ok.ru/profile/…",
    package_id=3845,
    objective="odkl_profile",
    default_cta="signUp",
    patterns=_patterns(
        CTA_PROFILE,
        TEXT_LONG,
        {
            189: "image_600x600",
            188: "image_1080x607",
            191: "video_square_300s",
            190: "video_landscape_300s",
        },
    ),
)

SURFACES: tuple[Surface, ...] = (
    VK_COMMUNITY,
    VK_PERSONAL,
    VK_NEWSLETTER,
    VK_CHANNEL,
    MAX_CHANNEL,
    OK_COMMUNITY,
    OK_PROFILE,
)

_BY_KIND: dict[str, Surface] = {surface.kind: surface for surface in SURFACES}

# Все медиа-слоты, известные справочнику: белый список для тела объявления.
CONTENT_SLOTS: frozenset[str] = frozenset(
    {ICON_SLOT} | {pattern.media_slot for surface in SURFACES for pattern in surface.patterns}
)


def surface_for(kind: str) -> Surface:
    """Площадка по ключу из брифа. Неизвестный ключ — сообщество ВК (самый частый случай)."""
    return _BY_KIND.get(kind, VK_COMMUNITY)


def known_kind(kind: str) -> bool:
    return kind in _BY_KIND


def ratio_of(width: int, height: int) -> str:
    """Ближайшее из поддерживаемых VK соотношений сторон."""
    value = width / height
    return min(RATIO_VALUES, key=lambda name: abs(RATIO_VALUES[name] - value))


def pick_pattern(surface: Surface, *, ratio: str, is_video: bool) -> Pattern:
    """Подобрать шаблон площадки под соотношение сторон присланного креатива.

    Точное совпадение соотношения — приоритет. Если его нет, берём ближайшее из
    доступных: у Одноклассников, например, вертикали не существует вовсе, и
    вертикальный кадр придётся вписать в квадрат, а не отказывать клиенту.
    """
    allowed = surface.patterns_for(is_video=is_video)
    if not allowed:
        kind = "видео" if is_video else "картинку"
        raise ValueError(f"Площадка «{surface.title}» не принимает {kind}")

    exact = [pattern for pattern in allowed if pattern.ratio == ratio]
    if exact:
        # Из нескольких длительностей берём самую длинную — она не ограничивает ролик.
        return max(exact, key=lambda pattern: pattern.max_seconds or 0)

    target = RATIO_VALUES[ratio]
    return min(allowed, key=lambda pattern: abs(RATIO_VALUES[pattern.ratio] - target))


def slot_size(slot: str) -> tuple[int, int] | None:
    """Точный размер картиночного слота; у видео размер не фиксирован."""
    return IMAGE_SLOT_SIZES.get(slot)


def text_limit(slot: str) -> int:
    """Лимит длины текстового слота."""
    return TEXT_SLOT_LIMITS.get(slot, 2000)
