"""Каноническая нумерованная карта полей брифа (для карточки и правок `номер.значение`).

Одна и та же нумерация используется и для показа карточки брифа оператору, и для
правок формата `номер.значение` (`services/edit_parser.py`). Порядок полей повторяет
порядок веб-форм (`web/static/brief-*.html`), ключ поля == `name` инпута == ключ
`Brief.payload` == внутренний id в `services/brief_parser.py`.

Карточка показывает ВСЕ канонические поля варианта (пустое → пустая строка), поэтому
номера стабильны независимо от того, что клиент оставил незаполненным.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BriefField:
    """Поле брифа: `key` — ключ payload, `label` — подпись для оператора."""

    key: str
    label: str


# Порядок = порядок полей формы web/static/brief-individual.html.
INDIVIDUAL_FIELDS: list[BriefField] = [
    BriefField("full_name", "Как обращаться"),
    BriefField("object_url", "Ссылка на страницу VK"),
    BriefField("vk_ad_cabinet_id", "ID кабинета VK Реклама"),
    BriefField("email", "Email"),
    BriefField("phone", "Телефон"),
    BriefField("telegram", "Telegram"),
    BriefField("audience_description", "Кого привлекаем"),
    BriefField("geo", "География"),
    BriefField("gender", "Пол аудитории"),
    BriefField("age_from", "Возраст от"),
    BriefField("age_to", "Возраст до"),
    BriefField("budget", "Бюджет"),
    BriefField("term", "Срок / период"),
    BriefField("target_type", "Куда привлекаем"),
    BriefField("materials", "Рекламные материалы"),
]

# Порядок = порядок полей формы web/static/brief-community.html.
COMMUNITY_FIELDS: list[BriefField] = [
    BriefField("full_name", "Контактное лицо"),
    BriefField("object_url", "Ссылка на сообщество VK"),
    BriefField("vk_ad_cabinet_id", "ID кабинета VK Реклама"),
    BriefField("email", "Email"),
    BriefField("phone", "Телефон"),
    BriefField("telegram", "Telegram"),
    BriefField("niche", "Ниша / сфера"),
    BriefField("org_type", "Форма организации"),
    BriefField("product_description", "Что продвигаем"),
    BriefField("site_url", "Сайт"),
    BriefField("usp", "Чем лучше конкурентов"),
    BriefField("audience_description", "Кого привлекаем"),
    BriefField("geo", "География"),
    BriefField("gender", "Пол аудитории"),
    BriefField("budget", "Бюджет"),
    BriefField("term", "Срок / период"),
]

_FIELDS_BY_VARIANT: dict[str, list[BriefField]] = {
    "individual": INDIVIDUAL_FIELDS,
    "community": COMMUNITY_FIELDS,
}


def fields_for(variant: str) -> list[BriefField]:
    """Канонический список полей варианта. `ValueError` — неизвестный вариант."""
    try:
        return _FIELDS_BY_VARIANT[variant]
    except KeyError:
        raise ValueError(f"Unknown brief variant: {variant}") from None


def numbered(payload: Mapping[str, str], variant: str) -> list[tuple[int, BriefField, str]]:
    """Пронумерованные поля `(номер, поле, значение)` — для карточки брифа.

    Номер — с 1, по порядку формы; значение берётся из payload (пустое → "").
    """
    return [
        (index, field, (payload.get(field.key) or "").strip())
        for index, field in enumerate(fields_for(variant), start=1)
    ]


def apply_edits(
    payload: Mapping[str, str], variant: str, edits: Mapping[int, str]
) -> tuple[dict[str, str], list[int]]:
    """Применить правки `{номер: значение}` к payload.

    Возвращает новый payload и отсортированный список неизвестных номеров (вне диапазона
    полей варианта), чтобы бот мог переспросить оператора.
    """
    fields = fields_for(variant)
    new_payload = dict(payload)
    unknown: list[int] = []
    for number, value in edits.items():
        if 1 <= number <= len(fields):
            new_payload[fields[number - 1].key] = value
        else:
            unknown.append(number)
    return new_payload, sorted(unknown)
