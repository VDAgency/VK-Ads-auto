"""Разбор ответа брифа в структурированные поля (Фаза 1, BRIEF_SPEC §9 вход).

Парсер не зависит от источника и не знает про VK: он принимает «сырой» ответ
как отображение `внутренний_id -> строка` и возвращает типизированную структуру
`ParsedBrief`. Ответ приходит из нашей веб-формы через `POST /api/v1/briefs`,
раскладка в параметры VK — `services/mapping.py` (Фаза 4).

Цель кампании (`Goal`) не спрашивается отдельным полем — она выводится из
выбранной клиентом площадки (`target_type`) функцией `services.goals.goal_for_target_type`:
площадка «лид-форма» ведёт к `Goal.LEAD_FORM`, площадка «сообщения» — к `Goal.MESSAGES`
(площадка прошла боевой зонд 2026-08-23, `integrations.vk_surfaces.VK_MESSAGES.verified=True`),
площадка «заявка через Senler» — к `Goal.SENLER` (технически тот же пакет VK, что и у
«Сообщений», `integrations.vk_surfaces.VK_SENLER`; собственный боевой прогон под именем
Senler проведён 2026-08-24, `verified=True`), все остальные — к `Goal.SUBSCRIBERS`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import Enum


class BriefVariant(Enum):
    """Вариант брифа: физлицо (личная страница) или ИП/бизнес (сообщество)."""

    INDIVIDUAL = "individual"
    COMMUNITY = "community"


class Goal(Enum):
    """Цель кампании. Выводится из площадки брифа (`services.goals.goal_for_target_type`).

    Реализованы и запускаются все четыре: подписчики (на любую из площадок подписки),
    заявки через лид-форму, сообщения сообществу и заявка через Senler. `MESSAGES`
    прошла боевой зонд 2026-08-23 (`integrations.vk_surfaces.VK_MESSAGES.verified=True`)
    и клиенту предлагается. `SENLER` технически работает тем же пакетом VK, что и
    «Сообщения» (боевая кампания 28694299 подтвердила пакет 3127 напрямую из VK,
    2026-08-24), и собственный боевой прогон под именем Senler руководитель тоже
    провёл 2026-08-24 (`integrations.vk_surfaces.VK_SENLER.verified=True`) — площадка
    предлагается клиенту наравне с остальными.
    """

    SUBSCRIBERS = "subscribers"
    LEAD_FORM = "lead_form"
    MESSAGES = "messages"
    SENLER = "senler"


class Gender(Enum):
    """Пол целевой аудитории."""

    MALE = "male"
    FEMALE = "female"
    ANY = "any"


class TargetType(Enum):
    """Куда привлекаем подписчиков — площадка подписки.

    Значения совпадают с ключами справочника площадок `integrations/vk_surfaces.py`:
    именно по ним адаптер выбирает пакет VK, цель кампании и шаблоны объявления.
    Подписка возможна на шесть разных площадок, а не только на сообщество и страницу
    (живой справочник пакетов VK, 2026-07-27).
    """

    PERSONAL_PAGE = "personal_page"
    COMMUNITY = "community"
    NEWSLETTER = "newsletter"
    VK_CHANNEL = "vk_channel"
    MAX_CHANNEL = "max_channel"
    OK_COMMUNITY = "ok_community"
    OK_PROFILE = "ok_profile"
    DZEN_CHANNEL = "dzen_channel"
    # Смежные цели: продвижение готового объекта, сбор заявок и сообщения сообществу.
    VK_POST_COMMUNITY = "vk_post_community"
    VK_POST_PERSONAL = "vk_post_personal"
    VK_POST_PROMOTED = "vk_post_promoted"
    VK_MUSIC = "vk_music"
    VK_CLIP = "vk_clip"
    LEAD_FORM = "lead_form"
    # Прошла боевой зонд 2026-08-23 (integrations.vk_surfaces.VK_MESSAGES.verified=True).
    MESSAGES = "messages"
    # Тот же пакет VK, что у MESSAGES (integrations.vk_surfaces.VK_SENLER) — собственный
    # боевой прогон под именем Senler проведён 2026-08-24, verified=True.
    SENLER = "senler"


class OrgType(Enum):
    """Тип организации клиента (для оформления кабинета и документов)."""

    INDIVIDUAL = "individual"
    SELF_EMPLOYED = "self_employed"
    SOLE_TRADER = "sole_trader"
    LEGAL_ENTITY = "legal_entity"
    FOREIGN_LEGAL = "foreign_legal"
    FOREIGN_INDIVIDUAL = "foreign_individual"


class BriefValidationError(Exception):
    """Бриф не содержит обязательных полей. `missing` — список их id."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"Brief is missing required fields: {', '.join(missing)}")


@dataclass(frozen=True)
class Contact:
    """Контакты идентификации клиента. Email и телефон обязательны (идентификация
    кабинета — по email, spec кабинета §4.1); telegram — опционально."""

    email: str | None = None
    phone: str | None = None
    telegram: str | None = None


@dataclass(frozen=True)
class Audience:
    """Параметры аудитории (вход для гео/демо-таргетинга)."""

    description: str
    geo: str
    gender: Gender = Gender.ANY
    age_from: int | None = None
    age_to: int | None = None
    exclusions: str | None = None


@dataclass(frozen=True)
class Budget:
    """Рекламный бюджет. При выборе «обсудить» сумма не задана."""

    amount_rub: int | None
    needs_discussion: bool
    term: str | None = None


@dataclass(frozen=True)
class Materials:
    """Наличие рекламных материалов у клиента (сам приём — в боте, Фаза 5)."""

    has_photo: bool = False
    has_video: bool = False
    needs_help: bool = False
    url: str | None = None


@dataclass(frozen=True)
class ParsedBrief:
    """Разобранный бриф. Бизнес-поля заполнены только у варианта `COMMUNITY`."""

    variant: BriefVariant
    goal: Goal
    full_name: str
    object_url: str
    target_type: TargetType
    contact: Contact
    audience: Audience
    budget: Budget
    materials: Materials
    competitors: list[str] = field(default_factory=list)
    extra: str | None = None
    # Поля варианта COMMUNITY (у физлица — None).
    company: str | None = None
    niche: str | None = None
    org_type: OrgType | None = None
    tax_id: str | None = None
    org_name: str | None = None
    bank_details: str | None = None
    site_url: str | None = None
    product_description: str | None = None
    avg_check: str | None = None
    usp: str | None = None
    offers: str | None = None


# --- нормализующие хелперы --------------------------------------------------


def _clean(value: str | None) -> str:
    """Обрезать пробелы; None → пустая строка."""
    return (value or "").strip()


def parse_budget(value: str) -> tuple[int | None, bool]:
    """Разобрать строку бюджета. Возвращает (сумма ₽ | None, нужно_обсуждение).

    «5 000 ₽» → (5000, False); «до 50 000 ₽» → (50000, False);
    «Готов(а) обсудить» / пусто → (None, True).
    """
    digits = re.sub(r"\D", "", _clean(value))
    if not digits:
        return None, True
    return int(digits), False


def normalize_phone(value: str) -> str | None:
    """Привести телефон к виду `+7XXXXXXXXXX`. Невалидный → None."""
    digits = re.sub(r"\D", "", _clean(value))
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return None
    return "+" + digits


def normalize_telegram(value: str) -> str | None:
    """Привести Telegram к `@username`. Срезает url-префиксы. Пусто → None."""
    raw = _clean(value)
    if not raw:
        return None
    raw = re.sub(r"^(https?://)?(t\.me/|telegram\.me/)", "", raw, flags=re.IGNORECASE)
    raw = raw.lstrip("@")
    if not raw:
        return None
    return "@" + raw


def parse_gender(value: str) -> Gender:
    """Разобрать пол. Пусто/нераспознано → `Gender.ANY`."""
    text = _clean(value).lower()
    if "муж" in text:
        return Gender.MALE
    if "жен" in text:
        return Gender.FEMALE
    return Gender.ANY


def parse_age(age_from: str, age_to: str) -> tuple[int | None, int | None]:
    """Разобрать диапазон возраста. Нечисловое → None для соответствующей границы."""

    def _to_int(value: str) -> int | None:
        digits = re.sub(r"\D", "", _clean(value))
        return int(digits) if digits else None

    return _to_int(age_from), _to_int(age_to)


def parse_materials(value: str) -> Materials:
    """Разобрать ответ о наличии материалов (по ключевым словам, эмодзи игнорируются)."""
    text = _clean(value).lower()
    if "ничего нет" in text or "нужна помощь" in text:
        return Materials(needs_help=True)
    return Materials(has_photo="фото" in text, has_video="видео" in text)


def parse_target_type(value: str) -> TargetType:
    """Разобрать «куда привлекаем» — площадку подписки.

    Порядок проверок важен: «сообщество в Одноклассниках» обязано попасть в ОК, а не в
    ВК, поэтому площадка ОК распознаётся раньше общего слова «сообщество». Пустое или
    непонятое значение — личная страница, как было исторически.
    """
    text = _clean(value).lower()

    # Senler — самостоятельная цель (тот же пакет VK, что у «Сообщений»). Проверяем
    # РАНЬШЕ лид-формы: естественная фраза «заявка через Senler» содержит «заявк»,
    # и без этого порядка утекла бы в TargetType.LEAD_FORM.
    if "senler" in text or "сенлер" in text:
        return TargetType.SENLER
    # Смежные цели распознаём раньше подписных: «пост сообщества» — это пост, а не
    # сообщество, и «лид-форма» не имеет отношения к площадкам подписки.
    if "лид" in text or "форма" in text or "заявк" in text:
        return TargetType.LEAD_FORM
    # «Сообщени…» (написать сообщение) — самостоятельная смежная цель, а не опечатка
    # в слове «сообщество» (буквосочетания не пересекаются, порядок проверки неважен).
    # Площадка прошла боевой зонд 2026-08-23 (integrations.vk_surfaces.VK_MESSAGES
    # verified=True) и предлагается клиенту в форме брифа как готовая опция
    # (web/lib/briefSurfaces.ts, «написать сообщение», enabled: true) — клиент
    # выбирает её сам, а не только оператор вручную задним числом.
    if "сообщени" in text:
        return TargetType.MESSAGES
    if "клип" in text:
        return TargetType.VK_CLIP
    if "музык" in text or "трек" in text:
        return TargetType.VK_MUSIC
    if "пост" in text:
        if "сайт" in text or "переход" in text:
            return TargetType.VK_POST_PROMOTED
        if "страниц" in text or "личн" in text:
            return TargetType.VK_POST_PERSONAL
        return TargetType.VK_POST_COMMUNITY

    is_ok = "однокласс" in text or "ok.ru" in text or "ок " in f" {text}"
    if is_ok:
        if "профил" in text or "страниц" in text:
            return TargetType.OK_PROFILE
        return TargetType.OK_COMMUNITY

    if "рассылк" in text:
        return TargetType.NEWSLETTER
    if "дзен" in text or "dzen" in text:
        return TargetType.DZEN_CHANNEL
    if "max" in text or "макс" in text:
        return TargetType.MAX_CHANNEL
    if "канал" in text:
        return TargetType.VK_CHANNEL
    if "сообществ" in text or "группа" in text or "группу" in text:
        return TargetType.COMMUNITY
    return TargetType.PERSONAL_PAGE


def parse_org_type(value: str) -> OrgType | None:
    """Разобрать тип организации. Нераспознано/пусто → None."""
    text = _clean(value).lower()
    if not text:
        return None
    if "самозан" in text:
        return OrgType.SELF_EMPLOYED
    if "ип" in text and "иностр" not in text:
        return OrgType.SOLE_TRADER
    if "ооо" in text or "юр" in text:
        if "иностр" in text:
            return OrgType.FOREIGN_LEGAL
        return OrgType.LEGAL_ENTITY
    if "иностр" in text and "физ" in text:
        return OrgType.FOREIGN_INDIVIDUAL
    if "иностр" in text:
        return OrgType.FOREIGN_LEGAL
    if "физ" in text:
        return OrgType.INDIVIDUAL
    return None


def split_competitors(value: str) -> list[str]:
    """Разбить список конкурентов на строки, выкинув пустые."""
    return [line.strip() for line in _clean(value).splitlines() if line.strip()]


# --- основной разбор --------------------------------------------------------

# Обязательные поля по варианту (BRIEF_SPEC §5.1). Контакт проверяется отдельно.
_COMMON_REQUIRED = (
    "full_name",
    "object_url",
    "audience_description",
    "geo",
    "budget",
    "term",
)
_COMMUNITY_REQUIRED = ("niche", "org_type", "product_description")


def _collect_missing(
    raw: Mapping[str, str], variant: BriefVariant, *, require_tax_id: bool
) -> list[str]:
    required = list(_COMMON_REQUIRED)
    if variant is BriefVariant.INDIVIDUAL:
        required.append("target_type")
    if variant is BriefVariant.COMMUNITY:
        required.extend(_COMMUNITY_REQUIRED)
    # ИНН обязателен у ОБОИХ вариантов (решение 2026-08-25: без него нельзя
    # завести клиенту рекламный кабинет — закон о рекламе требует указывать
    # конечного рекламодателя). Флаг, а не безусловное добавление в
    # `_COMMON_REQUIRED`, потому что этот же список проверяет и `parse_brief`
    # уже сохранённых брифов при запуске кампании (`launch_service.py`), а у
    # части старых брифов физлица ИНН не спрашивали вовсе — им нельзя внезапно
    # отказывать в запуске. Умолчание `require_tax_id=True` в `parse_brief`
    # ниже — сознательно строгое (закрывающее, а не открывающее): забытый на
    # новом пути приёма флаг обязан включить проверку, а не молча пропустить
    # бриф без ИНН. Единственное послабление — явный `require_tax_id=False` у
    # разбора уже сохранённых брифов (см. `launch_service.py`).
    if require_tax_id:
        required.append("tax_id")
    missing = [key for key in required if not _clean(raw.get(key))]
    # Идентификация кабинета — по email, поэтому email И телефон обязательны
    # (решение 2026-07-17, spec кабинета §4.1). Telegram — опционально.
    if not _clean(raw.get("email")):
        missing.append("email")
    if not _clean(raw.get("phone")):
        missing.append("phone")
    return missing


def parse_brief(
    raw: Mapping[str, str], variant: BriefVariant, *, require_tax_id: bool = True
) -> ParsedBrief:
    """Разобрать сырой бриф в `ParsedBrief`. Бросает `BriefValidationError` при нехватке.

    `raw` — отображение «внутренний id поля -> строковое значение» (как ячейка формы).
    `variant` определяет набор обязательных полей и наличие бизнес-секции.
    `require_tax_id` — требовать ИНН как обязательное поле. По умолчанию `True`
    (строго): так безопаснее для ЛЮБОГО пути приёма нового брифа, включая ещё
    не написанные (бот, реимпорт, админ-форма) — забытый флаг закрывает, а не
    открывает. Единственное место, где нужно явное послабление —
    `services/launch_service.py`: он разбирает уже СОХРАНЁННЫЙ бриф при
    запуске кампании, а среди старых брифов физлиц есть такие, где ИНН не
    спрашивали вовсе; передаёт `require_tax_id=False` явно, с комментарием на
    месте вызова.
    """
    # Локальный импорт: `services.goals` сам импортирует `Goal`/`TargetType` отсюда
    # же, импорт наверху файла закольцевал бы модули друг на друга.
    from services.goals import goal_for_target_type

    missing = _collect_missing(raw, variant, require_tax_id=require_tax_id)
    if missing:
        raise BriefValidationError(missing)

    def get(key: str) -> str:
        return _clean(raw.get(key))

    amount, needs_discussion = parse_budget(get("budget"))
    age_from, age_to = parse_age(get("age_from"), get("age_to"))

    # Площадку выбирает клиент в обеих формах брифа: подписка возможна не только на
    # сообщество. Пустое поле у бизнес-брифа — сообщество, у физлица — личная страница:
    # так ведут себя значения по умолчанию в формах.
    raw_target = get("target_type")
    if raw_target:
        target_type = parse_target_type(raw_target)
    elif variant is BriefVariant.COMMUNITY:
        target_type = TargetType.COMMUNITY
    else:
        target_type = TargetType.PERSONAL_PAGE

    contact = Contact(
        email=get("email") or None,
        phone=normalize_phone(get("phone")),
        telegram=normalize_telegram(get("telegram")),
    )
    audience = Audience(
        description=get("audience_description"),
        geo=get("geo"),
        gender=parse_gender(get("gender")),
        age_from=age_from,
        age_to=age_to,
        exclusions=get("exclusions") or None,
    )
    budget = Budget(
        amount_rub=amount,
        needs_discussion=needs_discussion,
        term=get("term") or None,
    )
    materials = replace(parse_materials(get("materials")), url=get("materials_url") or None)

    is_community = variant is BriefVariant.COMMUNITY

    def biz(key: str) -> str | None:
        """Бизнес-поле: значение только для варианта COMMUNITY, иначе None."""
        return (get(key) or None) if is_community else None

    return ParsedBrief(
        variant=variant,
        goal=goal_for_target_type(target_type),
        full_name=get("full_name"),
        object_url=get("object_url"),
        target_type=target_type,
        contact=contact,
        audience=audience,
        budget=budget,
        materials=materials,
        competitors=split_competitors(get("competitors")),
        extra=get("extra") or None,
        # ИНН спрашивают ОБА макета (обязателен на приёме нового брифа, см.
        # `require_tax_id` выше), поэтому поле не бизнес-только.
        tax_id=get("tax_id") or None,
        company=biz("company"),
        niche=biz("niche"),
        org_type=parse_org_type(get("org_type")) if is_community else None,
        org_name=biz("org_name"),
        # Реквизиты в бриф больше не собираются (BRIEF_SPEC §0: не параметр VK и не
        # идентификация) — их место в личном кабинете. Поле оставлено, чтобы разбор
        # ранее сохранённых брифов не терял данные.
        bank_details=biz("bank_details"),
        site_url=biz("site_url"),
        product_description=biz("product_description"),
        avg_check=biz("avg_check"),
        usp=biz("usp"),
        offers=biz("offers"),
    )
