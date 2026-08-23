"""Алгоритмическая раскладка брифа в спецификацию кампании VK (Фаза 4).

Без ИИ: правила маппинга `ParsedBrief` → нейтральная `CampaignSpec`. Спека —
промежуточное представление, которое `VkApiAdapter` переводит в тело VK Ads API
(`targetings`, бюджет). Преобразование гео-текста в числовые region id и подбор
`package_id`/реального VK objective — на стороне адаптера по площадке
(`integrations.vk_api.campaign_objective`, живой API, см. docs/VK_API_REFERENCE.md):
поле `objective` здесь — неопределённое значение по умолчанию, адаптер его правит.
Поддержаны две цели — подписчики и заявки через лид-форму (`services.brief_parser.Goal`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from services.brief_parser import Gender, Goal, ParsedBrief, TargetType

# Цель «подписчики/вступления в сообщество» в VK Ads API.
SOCIAL_ENGAGEMENT = "socialengagement"

# Площадка подписки — нейтральные значения, общие для любых рекламных систем.
# Пусто = неизвестно (адаптер догадывается по ссылке, как раньше).
OBJECT_KIND_COMMUNITY = TargetType.COMMUNITY.value
OBJECT_KIND_PERSONAL = TargetType.PERSONAL_PAGE.value
OBJECT_KIND_NEWSLETTER = TargetType.NEWSLETTER.value
OBJECT_KIND_VK_CHANNEL = TargetType.VK_CHANNEL.value
OBJECT_KIND_MAX_CHANNEL = TargetType.MAX_CHANNEL.value
OBJECT_KIND_OK_COMMUNITY = TargetType.OK_COMMUNITY.value
OBJECT_KIND_OK_PROFILE = TargetType.OK_PROFILE.value
OBJECT_KIND_DZEN_CHANNEL = TargetType.DZEN_CHANNEL.value
OBJECT_KIND_VK_POST_COMMUNITY = TargetType.VK_POST_COMMUNITY.value
OBJECT_KIND_VK_POST_PERSONAL = TargetType.VK_POST_PERSONAL.value
OBJECT_KIND_VK_POST_PROMOTED = TargetType.VK_POST_PROMOTED.value
OBJECT_KIND_VK_MUSIC = TargetType.VK_MUSIC.value
OBJECT_KIND_VK_CLIP = TargetType.VK_CLIP.value
OBJECT_KIND_LEAD_FORM = TargetType.LEAD_FORM.value

_VK_AGE_MIN = 14
_VK_AGE_MAX = 75

# Цели, для которых раскладка уже реализована, и заголовок кампании под каждую —
# чтобы кампания на заявки не называлась «Подписчики · …» (вводит в заблуждение).
_SUPPORTED_GOALS = (Goal.SUBSCRIBERS, Goal.LEAD_FORM)
_GOAL_NAME_PREFIX: dict[Goal, str] = {
    Goal.SUBSCRIBERS: "Подписчики",
    Goal.LEAD_FORM: "Заявки",
}


class UnsupportedBriefGoalError(Exception):
    """Цель брифа (`ParsedBrief.goal`) не поддержана раскладкой в `CampaignSpec`.

    Сейчас это только `Goal.MESSAGES`: площадка «Сообщения» заведена в перечисление
    (`services.brief_parser.Goal`), но не прошла боевую проверку и клиенту не
    предлагается — однако бриф с ней всё равно можно прислать напрямую в API, минуя
    веб-форму. `services.launch_service.launch_from_creative` ловит эту ошибку и
    транслирует в свой `UnsupportedGoalError`, чтобы роутеры и бот отвечали честным
    422, а не 500 с трассировкой (не путать с `UnsupportedGoalError`: тот — про
    параметр `goal`, который оператор передаёт явно при запуске).
    """

    def __init__(self, goal: Goal) -> None:
        self.goal = goal
        super().__init__(f"Unsupported goal: {goal}")


@dataclass(frozen=True)
class CampaignSpec:
    """Нейтральная спецификация кампании (вход для адаптера площадки)."""

    objective: str
    name: str
    object_url: str
    geo_raw: str  # текстом из брифа; в region id переводит адаптер (live API)
    # Тип объекта из брифа: короткий адрес (vk.ru/имя) сам по себе человека от
    # сообщества не отличает, поэтому подсказка из брифа авторитетнее ссылки.
    object_kind: str = ""
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
    """Разложить разобранный бриф в спецификацию кампании (подписчики или лид-форма)."""
    if brief.goal not in _SUPPORTED_GOALS:
        raise UnsupportedBriefGoalError(brief.goal)

    audience = brief.audience
    name = f"{_GOAL_NAME_PREFIX[brief.goal]} · {brief.full_name}".strip()
    return CampaignSpec(
        objective=SOCIAL_ENGAGEMENT,
        name=name,
        object_url=brief.object_url,
        geo_raw=audience.geo,
        object_kind=brief.target_type.value,
        age_list=_age_list(audience.age_from, audience.age_to),
        sex=_sex(audience.gender),
        budget_rub=brief.budget.amount_rub,
        needs_budget_discussion=brief.budget.needs_discussion,
    )
