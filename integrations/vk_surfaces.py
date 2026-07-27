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
    # Дзен: свои имена слотов и заметно более жёсткие лимиты.
    "name_140": 140,
    "title_25": 25,
    "text_40": 40,
    # Лид-формы: у них самый богатый набор обязательных текстов.
    "text_220": 220,
    "text_long": 2000,
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

# Слот ссылки на объект рекламы. У всех площадок это `primary`, и только Дзен
# требует собственный слот.
URL_SLOT_PRIMARY = "primary"
URL_SLOT_DZEN = "dzen_publication"
# Продвижение уже существующего поста, клипа или трека: объявлением служит сам объект.
URL_SLOT_POST = "vk_post"
URL_SLOT_CLIP = "vk_clip"

# Режим ставок. У подписных и лидовых пакетов это оптимизация под цель, у брендовых
# (клипы) — фиксированная ставка: `max_goals` там отвергается как `unallowed_value`.
BIDDING_MAX_GOALS = "max_goals"
BIDDING_FIXED = "fixed"

# Цель кампании — по ней интерфейсы группируют площадки.
GOAL_SUBSCRIPTION = "subscription"
GOAL_ENGAGEMENT = "engagement"
GOAL_LEADS = "leads"

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
    """Один разрешённый шаблон объявления: основной медиа-слот и его текстовая обвязка.

    Имена текстовых слотов различаются по площадкам, а у Дзена кнопки нет вовсе —
    поэтому слоты хранятся здесь, а не берутся из общих констант.
    """

    pattern_id: int
    media_slot: str
    cta_slot: str | None
    text_slot: str
    title_slot: str = SLOT_TITLE
    # Слоты, обязательные сверх заголовка, текста и кнопки: имя канала у Дзена,
    # короткое описание и подпись под кнопкой у лид-форм. Пропуск любого из них —
    # `bad_value: At least one pattern must be in package's settings`.
    extra_slots: tuple[str, ...] = ()

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
    url_slot: str = URL_SLOT_PRIMARY
    goal: str = GOAL_SUBSCRIPTION
    autobidding: str = BIDDING_MAX_GOALS
    # Продвижение готового поста креатива не требует: объявлением служит сам пост,
    # и VK принимает объявление вообще без содержимого и текстов.
    needs_creative: bool = True
    # Прошла ли площадка боевое создание кампании в живом кабинете. Непроверенные
    # в интерфейсе показываются как «скоро» и клиенту не предлагаются.
    verified: bool = False

    @property
    def ratios(self) -> tuple[str, ...]:
        """Соотношения сторон, которые площадка вообще принимает."""
        return tuple(sorted({pattern.ratio for pattern in self.patterns}))

    def patterns_for(self, *, is_video: bool) -> tuple[Pattern, ...]:
        return tuple(pattern for pattern in self.patterns if pattern.is_video is is_video)

    @property
    def default_pattern(self) -> Pattern | None:
        """Шаблон, когда креатива ещё нет: нужен ради имён слотов кнопки и текста.

        Первым в каждом наборе идёт квадратная картинка — самый нейтральный вариант.
        У площадок продвижения поста шаблонов нет вовсе, и это не ошибка.
        """
        return self.patterns[0] if self.patterns else None


def _patterns(
    cta: str | None,
    text: str,
    table: dict[int, str],
    *,
    title: str = SLOT_TITLE,
    extra: tuple[str, ...] = (),
) -> tuple[Pattern, ...]:
    """Собрать шаблоны площадки из таблицы «id шаблона → основной медиа-слот»."""
    return tuple(
        Pattern(pattern_id, slot, cta, text, title, extra) for pattern_id, slot in table.items()
    )


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
    verified=True,
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
    verified=True,
    patterns=_CHANNEL_PATTERNS,
)

MAX_CHANNEL = Surface(
    kind="max_channel",
    title="Канал MAX",
    hint="ссылка на канал MAX",
    package_id=4686,
    objective="max_channel",
    default_cta="visitSite",
    verified=True,
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
    verified=True,
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
    verified=True,
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

# --- Дзен: подписка на канал (пакет 3642) ------------------------------------------
# Площадка живёт по своим правилам: ссылка идёт в слот `dzen_publication`, кнопки нет
# вовсе, а тексты втрое короче привычных. И главное — минимальный дневной бюджет
# 10 000 ₽ против 100 ₽ у всех остальных площадок (боевая проверка 2026-07-27),
# поэтому предлагать её клиенту с бюджетом «5 000 ₽ на месяц» бессмысленно.
DZEN_CHANNEL = Surface(
    kind="dzen_channel",
    title="Канал Дзен",
    hint="ссылка на канал Дзен: dzen.ru/…",
    package_id=3642,
    objective="dzen",
    default_cta="",
    verified=True,
    url_slot=URL_SLOT_DZEN,
    patterns=_patterns(
        None,
        "text_40",
        {
            578: "image_600x600",
            580: "image_1080x607",
            581: "image_1080x1350",
        },
        title="title_25",
        extra=("name_140",),
    ),
)

# --- Продвижение готового поста, клипа и трека -------------------------------------
# Отдельное семейство: объявлением служит сам объект, поэтому креатив и тексты не
# нужны — VK принимает объявление с одной только ссылкой (боевая проверка 2026-07-27).
# Клиент присылает ссылку на конкретный пост, а не на сообщество.
VK_POST_COMMUNITY = Surface(
    kind="vk_post_community",
    title="Пост сообщества ВКонтакте",
    hint="ссылка на конкретный пост: vk.com/…?w=wall-123_456",
    package_id=3194,
    objective="socialengagement",
    default_cta="",
    verified=True,
    url_slot=URL_SLOT_POST,
    goal=GOAL_ENGAGEMENT,
    needs_creative=False,
    patterns=(),
)

VK_POST_PERSONAL = Surface(
    kind="vk_post_personal",
    title="Пост личной страницы ВКонтакте",
    hint="ссылка на конкретный пост страницы: vk.com/…?w=wall123_456",
    package_id=3270,
    objective="socialengagement_profile",
    default_cta="",
    verified=True,
    url_slot=URL_SLOT_POST,
    goal=GOAL_ENGAGEMENT,
    needs_creative=False,
    patterns=(),
)

VK_POST_PROMOTED = Surface(
    kind="vk_post_promoted",
    title="Пост с переходом на сайт",
    hint="ссылка на пост со ссылкой внутри: vk.com/…?w=wall-123_456",
    package_id=4654,
    objective="promoted_vk_post",
    default_cta="",
    verified=True,
    url_slot=URL_SLOT_POST,
    goal=GOAL_ENGAGEMENT,
    needs_creative=False,
    patterns=(),
)

VK_MUSIC = Surface(
    kind="vk_music",
    title="Музыка ВКонтакте",
    hint="ссылка на трек или подборку",
    package_id=3235,
    objective="socialaudio",
    default_cta="",
    verified=True,
    url_slot=URL_SLOT_POST,
    goal=GOAL_ENGAGEMENT,
    needs_creative=False,
    patterns=(),
)

# Клип идёт по брендовому пакету, а он не принимает оптимизацию под цель:
# `max_goals` → `autobidding_mode: unallowed_value`, работает только `fixed`.
# Единственный разрешённый шаблон — 582 `clip_vk`, и он требует ровно одно поле:
# ссылку в слоте `vk_clip`. Ни медиа, ни текстов, ни кнопки — конфигурация ниже
# совпадает с ним точно.
# ⚠️ `verified=False`: боевого создания не было. VK отвечает
# `Clip not found or not available` — нужен клип, доступный этому кабинету;
# случайный адрес не подходит. Код при этом уже верен, ждём только ссылку.
VK_CLIP = Surface(
    kind="vk_clip",
    title="Клип ВКонтакте",
    hint="ссылка на клип: vk.com/clip-123_456",
    package_id=3785,
    objective="branding_socialengagement",
    default_cta="",
    url_slot=URL_SLOT_CLIP,
    goal=GOAL_ENGAGEMENT,
    autobidding=BIDDING_FIXED,
    needs_creative=False,
    patterns=(),
)

# --- Лид-формы ---------------------------------------------------------------------
# Объект рекламы — лид-форма, созданная в интерфейсе VK: эндпоинта для её создания нет
# (`/lead_forms.json` и соседние → 404), поэтому оператор делает форму руками и
# присылает ссылку. У готовой формы адрес выглядит как `leadads://857898/`; обычная
# ссылка на сообщество тоже принимается, но ведёт не туда, куда ждёт клиент.
# Тексты богаче обычных: помимо заголовка нужны короткое описание и подпись
# под кнопкой — всего пять обязательных блоков.
LEAD_FORMS = Surface(
    kind="lead_form",
    title="Лид-форма ВКонтакте",
    hint="ссылка на лид-форму из кабинета VK (вида leadads://<номер>/)",
    package_id=3215,
    objective="leadads",
    default_cta="enroll",
    verified=True,
    goal=GOAL_LEADS,
    patterns=_patterns(
        "cta_leadads",
        "text_90",
        {
            507: "image_600x600",
            506: "image_1080x607",
            239: "image_607x1080",
            508: "image_1080x1350",
            503: "video_square_180s",
            505: "video_landscape_180s",
            502: "video_portrait_9_16_180s",
            504: "video_portrait_4_5_180s",
        },
        extra=("title_30_additional", "text_220"),
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
    DZEN_CHANNEL,
    VK_POST_COMMUNITY,
    VK_POST_PERSONAL,
    VK_POST_PROMOTED,
    VK_MUSIC,
    VK_CLIP,
    LEAD_FORMS,
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
