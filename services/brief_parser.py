"""Разбор ответа брифа в структурированные поля (Фаза 1, BRIEF_SPEC §9 вход).

Парсер не зависит от источника (Google Sheets / Яндекс) и не знает про VK: он
принимает «сырой» ответ как отображение `внутренний_id -> строка` и возвращает
типизированную структуру `ParsedBrief`. Маппинг колонок формы во внутренние id —
ответственность слоя чтения (`integrations/google_forms.py`), раскладка в параметры
VK — `services/mapping.py` (Фаза 4).

Скоуп сужен: единственная поддерживаемая цель — «подписчики» (`Goal.SUBSCRIBERS`).
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
    """Цель кампании. По сужению скоупа поддерживаются только подписчики."""

    SUBSCRIBERS = "subscribers"


class Gender(Enum):
    """Пол целевой аудитории."""

    MALE = "male"
    FEMALE = "female"
    ANY = "any"


class TargetType(Enum):
    """Объект рекламы: личная страница или сообщество/группа."""

    PERSONAL_PAGE = "personal_page"
    COMMUNITY = "community"


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
    vk_ad_cabinet_id: str | None = None
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
    """Разобрать «куда привлекаем». «Сообщество/группа» → COMMUNITY, иначе личная страница."""
    text = _clean(value).lower()
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
    "vk_ad_cabinet_id",
    "audience_description",
    "geo",
    "budget",
    "term",
)
_COMMUNITY_REQUIRED = ("niche", "org_type", "product_description")


def _collect_missing(raw: Mapping[str, str], variant: BriefVariant) -> list[str]:
    required = list(_COMMON_REQUIRED)
    if variant is BriefVariant.INDIVIDUAL:
        required.append("target_type")
    if variant is BriefVariant.COMMUNITY:
        required.extend(_COMMUNITY_REQUIRED)
    missing = [key for key in required if not _clean(raw.get(key))]
    # Идентификация кабинета — по email, поэтому email И телефон обязательны
    # (решение 2026-07-17, spec кабинета §4.1). Telegram — опционально.
    if not _clean(raw.get("email")):
        missing.append("email")
    if not _clean(raw.get("phone")):
        missing.append("phone")
    return missing


def parse_brief(raw: Mapping[str, str], variant: BriefVariant) -> ParsedBrief:
    """Разобрать сырой бриф в `ParsedBrief`. Бросает `BriefValidationError` при нехватке.

    `raw` — отображение «внутренний id поля -> строковое значение» (как ячейка формы).
    `variant` определяет набор обязательных полей и наличие бизнес-секции.
    """
    missing = _collect_missing(raw, variant)
    if missing:
        raise BriefValidationError(missing)

    def get(key: str) -> str:
        return _clean(raw.get(key))

    amount, needs_discussion = parse_budget(get("budget"))
    age_from, age_to = parse_age(get("age_from"), get("age_to"))

    if variant is BriefVariant.COMMUNITY:
        target_type = TargetType.COMMUNITY
    else:
        target_type = parse_target_type(get("target_type"))

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
        goal=Goal.SUBSCRIBERS,
        full_name=get("full_name"),
        object_url=get("object_url"),
        target_type=target_type,
        contact=contact,
        audience=audience,
        budget=budget,
        materials=materials,
        vk_ad_cabinet_id=get("vk_ad_cabinet_id") or None,
        competitors=split_competitors(get("competitors")),
        extra=get("extra") or None,
        company=biz("company"),
        niche=biz("niche"),
        org_type=parse_org_type(get("org_type")) if is_community else None,
        tax_id=biz("tax_id"),
        org_name=biz("org_name"),
        bank_details=biz("bank_details"),
        site_url=biz("site_url"),
        product_description=biz("product_description"),
        avg_check=biz("avg_check"),
        usp=biz("usp"),
        offers=biz("offers"),
    )
