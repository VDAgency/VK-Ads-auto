from typing import Any

from services.brief_parser import BriefVariant, parse_brief
from services.mapping import SOCIAL_ENGAGEMENT, build_campaign_spec

BASE = {
    "full_name": "Иван",
    "object_url": "https://vk.com/ivan",
    "vk_ad_cabinet_id": "13410929",
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
