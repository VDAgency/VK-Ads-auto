"""Каноническая нумерованная карта полей брифа (для карточки и правок `номер.значение`).

Одна и та же нумерация используется и для показа карточки брифа оператору, и для
правок формата `номер.значение` (`services/edit_parser.py`). Порядок полей повторяет
порядок веб-форм (`web/app/brief-*/page.tsx`), ключ поля == `name` инпута == ключ
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


# Порядок = порядок полей формы web/app/brief-individual/page.tsx, то есть порядок
# секций макета физлица (docs/BRIEF_FIELDS.md §1.1) с добавленным полем «ID кабинета
# VK Реклама» — оно наше, макета не касается, и стоит рядом со ссылкой на объект.
INDIVIDUAL_FIELDS: list[BriefField] = [
    # Секция 1 — контактная информация.
    BriefField("full_name", "Как обращаться"),
    BriefField("phone", "Телефон"),
    BriefField("telegram", "Telegram"),
    BriefField("email", "Email"),
    BriefField("tax_id", "ИНН"),
    # Секция 2 — страница ВКонтакте.
    BriefField("object_url", "Ссылка на страницу VK"),
    BriefField("vk_ad_cabinet_id", "ID кабинета VK Реклама"),
    BriefField("target_type", "Куда привлекаем"),
    # Секция 3 — аудитория.
    BriefField("audience_description", "Кого привлекаем"),
    BriefField("gender", "Пол аудитории"),
    BriefField("age_from", "Возраст от"),
    BriefField("age_to", "Возраст до"),
    BriefField("geo", "География"),
    # Секция 4 — бюджет и сроки.
    BriefField("budget", "Бюджет"),
    BriefField("term", "Срок / период"),
    # Секция 5 — рекламные материалы.
    BriefField("materials", "Рекламные материалы"),
    BriefField("materials_url", "Ссылка на материалы"),
    # Секция 6 — дополнительно.
    BriefField("competitors", "Конкуренты / примеры"),
    BriefField("extra", "Что ещё важно учесть"),
]

# Порядок = порядок полей формы web/app/brief-community/page.tsx, то есть порядок
# секций макета ИП (docs/BRIEF_FIELDS.md §1.2) с двумя отличиями:
#   • добавлено «ID кабинета VK Реклама» (наше поле, см. выше);
#   • НЕТ поля «Реквизиты для счёта» — банковские данные не относятся ни к параметрам
#     VK, ни к идентификации (BRIEF_SPEC §0), поэтому в бриф не собираются; их место —
#     личный кабинет клиента (docs/ROADMAP.md, доработка кабинета).
COMMUNITY_FIELDS: list[BriefField] = [
    # Секция 1 — контактная информация.
    BriefField("full_name", "Контактное лицо"),
    BriefField("company", "Компания / проект"),
    BriefField("phone", "Телефон"),
    BriefField("telegram", "Telegram"),
    BriefField("email", "Email"),
    BriefField("niche", "Ниша / сфера"),
    BriefField("org_type", "Форма организации"),
    BriefField("tax_id", "ИНН / ОГРН / ОГРНИП"),
    BriefField("org_name", "Наименование организации"),
    # Секция 2 — продукт и объект рекламы.
    BriefField("object_url", "Ссылка на сообщество VK"),
    BriefField("vk_ad_cabinet_id", "ID кабинета VK Реклама"),
    BriefField("site_url", "Сайт"),
    BriefField("product_description", "Что продвигаем"),
    BriefField("avg_check", "Средний чек"),
    BriefField("usp", "Чем лучше конкурентов"),
    BriefField("offers", "Акции и спецпредложения"),
    # Секция 3 — аудитория.
    BriefField("audience_description", "Кого привлекаем"),
    BriefField("gender", "Пол аудитории"),
    BriefField("age_from", "Возраст от"),
    BriefField("age_to", "Возраст до"),
    BriefField("geo", "География"),
    BriefField("exclusions", "Кому не показывать"),
    # Секция 4 — цель рекламы. Пока запускается только «подписчики»; остальные цели
    # показаны в форме заблокированными (web/lib/briefGoals.ts).
    BriefField("goal", "Цель рекламы"),
    # Секция 5 — бюджет и сроки.
    BriefField("budget", "Бюджет"),
    BriefField("term", "Срок / период"),
    # Секция 6 — рекламные материалы.
    BriefField("materials", "Рекламные материалы"),
    BriefField("materials_url", "Ссылка на материалы"),
    # Секция 7 — конкуренты.
    BriefField("competitors", "Конкуренты"),
    # Секция 8 — дополнительно.
    BriefField("extra", "Что ещё важно учесть"),
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
