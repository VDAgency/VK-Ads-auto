"""Алгоритмическая раскладка брифа в спецификацию кампании VK (Фаза 4).

Без ИИ: правила маппинга `ParsedBrief` → нейтральная `CampaignSpec`. Спека —
промежуточное представление, которое `VkApiAdapter` переводит в тело VK Ads API
(objective `socialengagement`, `targetings`, бюджет). Преобразование гео-текста в
числовые region id и подбор `package_id` — на стороне адаптера (живой API,
см. docs/VK_API_REFERENCE.md). Сейчас поддержана единственная цель — подписчики.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from services.brief_parser import Gender, Goal, ParsedBrief

# Цель «подписчики/вступления в сообщество» в VK Ads API.
SOCIAL_ENGAGEMENT = "socialengagement"

_VK_AGE_MIN = 14
_VK_AGE_MAX = 75


@dataclass(frozen=True)
class CampaignSpec:
    """Нейтральная спецификация кампании (вход для адаптера площадки)."""

    objective: str
    name: str
    object_url: str
    geo_raw: str  # текстом из брифа; в region id переводит адаптер (live API)
    age_list: list[int] = field(default_factory=list)  # пусто = без возрастного таргетинга
    sex: list[str] = field(default_factory=list)  # [] = любой; ["male"]/["female"]
    budget_rub: int | None = None
    needs_budget_discussion: bool = False


def _age_list(age_from: int | None, age_to: int | None) -> list[int]:
    """Развернуть диапазон возраста в список лет (VK age_list). Пусто, если не задан."""
    if age_from is None and age_to is None:
        return []
    low = max(_VK_AGE_MIN, age_from or _VK_AGE_MIN)
    high = min(_VK_AGE_MAX, age_to or _VK_AGE_MAX)
    if low > high:
        return []
    return list(range(low, high + 1))


def _sex(gender: Gender) -> list[str]:
    """Перевести пол брифа в значения VK (`male`/`female`); любой → []."""
    if gender is Gender.MALE:
        return ["male"]
    if gender is Gender.FEMALE:
        return ["female"]
    return []


def build_campaign_spec(brief: ParsedBrief) -> CampaignSpec:
    """Разложить разобранный бриф в спецификацию кампании на подписчиков."""
    if brief.goal is not Goal.SUBSCRIBERS:  # pragma: no cover - единственная цель MVP
        raise ValueError(f"Unsupported goal: {brief.goal}")

    audience = brief.audience
    name = f"Подписчики · {brief.full_name}".strip()
    return CampaignSpec(
        objective=SOCIAL_ENGAGEMENT,
        name=name,
        object_url=brief.object_url,
        geo_raw=audience.geo,
        age_list=_age_list(audience.age_from, audience.age_to),
        sex=_sex(audience.gender),
        budget_rub=brief.budget.amount_rub,
        needs_budget_discussion=brief.budget.needs_discussion,
    )
