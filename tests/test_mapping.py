from typing import Any

from services.brief_parser import BriefVariant, Goal, TargetType, parse_brief
from services.mapping import SOCIAL_ENGAGEMENT, build_campaign_spec

BASE = {
    "full_name": "Иван",
    "object_url": "https://vk.com/ivan",
    "audience_description": "целевая аудитория",
    "geo": "Москва",
    "budget": "30000",
    "term": "месяц",
    "target_type": "личная страница",
    "email": "i@e.com",
    "phone": "+79990000000",
    "gender": "мужской",
    "age_from": "18",
    "age_to": "24",
}


def _spec(overrides: dict[str, Any] | None = None) -> Any:
    brief = parse_brief({**BASE, **(overrides or {})}, BriefVariant.INDIVIDUAL)
    return build_campaign_spec(brief)


def test_objective_is_social_engagement() -> None:
    assert _spec().objective == SOCIAL_ENGAGEMENT


def test_name_and_object() -> None:
    spec = _spec()
    assert "Подписчики" in spec.name
    assert spec.object_url == "https://vk.com/ivan"
    assert spec.geo_raw == "Москва"


def test_age_range_expanded() -> None:
    assert _spec().age_list == list(range(18, 25))


def test_age_empty_when_absent() -> None:
    assert _spec({"age_from": "", "age_to": ""}).age_list == []


def test_age_clamped_to_vk_bounds() -> None:
    assert _spec({"age_from": "5", "age_to": "200"}).age_list == list(range(14, 76))


def test_sex_male() -> None:
    assert _spec().sex == ["male"]


def test_sex_any_when_unspecified() -> None:
    assert _spec({"gender": ""}).sex == []


def test_budget_passthrough() -> None:
    spec = _spec({"budget": "50000"})
    assert spec.budget_rub == 50000
    assert spec.needs_budget_discussion is False


def test_budget_discussion() -> None:
    spec = _spec({"budget": "готов обсудить"})
    assert spec.budget_rub is None
    assert spec.needs_budget_discussion is True


# --- цель «лид-форма» --------------------------------------------------------


def test_lead_form_goal_is_accepted_not_only_subscribers() -> None:
    """`build_campaign_spec` больше не требует именно подписчиков."""
    spec = _spec({"target_type": "📋 Лид-форма ВКонтакте", "object_url": "leadads://857898/"})
    assert spec.object_kind == TargetType.LEAD_FORM.value
    assert spec.object_url == "leadads://857898/"


def test_lead_form_name_says_zayavki_not_podpischiki() -> None:
    # Кампания на заявки не должна называться «Подписчики · …» — вводит в заблуждение.
    spec = _spec({"target_type": "📋 Лид-форма ВКонтакте"})
    assert "Заявки" in spec.name
    assert "Подписчики" not in spec.name


# --- цель «сообщения» (боевой зонд 2026-08-23 подтвердил пакет 3127) ----------


def test_messages_goal_is_accepted_not_only_subscribers_and_lead_form() -> None:
    """Бриф с площадкой «сообщения» больше не отклоняется: `VK_MESSAGES` прошла
    боевой зонд (`integrations.vk_surfaces.VK_MESSAGES.verified=True`), и раскладка
    строит спеку так же, как для подписчиков (тот же `objective`), просто с другим
    `object_kind` — адаптер уже по нему выбирает пакет 3127 и кнопку сообщества."""
    brief = parse_brief(
        {**BASE, "target_type": "написать сообщение", "object_url": "https://vk.com/community1"},
        BriefVariant.INDIVIDUAL,
    )
    assert brief.goal is Goal.MESSAGES

    spec = build_campaign_spec(brief)
    assert spec.object_kind == TargetType.MESSAGES.value
    assert spec.object_url == "https://vk.com/community1"


def test_messages_name_says_soobshcheniya_not_podpischiki() -> None:
    # Кампания на сообщения не должна называться «Подписчики · …» — вводит в заблуждение.
    spec = _spec({"target_type": "написать сообщение", "object_url": "https://vk.com/community1"})
    assert "Сообщения" in spec.name
    assert "Подписчики" not in spec.name
