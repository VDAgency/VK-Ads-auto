"""Банковские реквизиты клиента для документов (кабинет, spec 2026-09-19 §E).

Решение 2026-07-25: реквизиты живут в личном кабинете клиента, не в брифе (бриф —
параметры VK, реквизиты нужны только для оформления документов). Хранение —
открытым текстом (не секрет доступа, сервер в РФ), поэтому валидация формата и
контрольных сумм — единственная защита от опечаток, и её обязан пройти любой
путь сохранения (`save_client_bank_details`).

Контрольная сумма ЦБ — общий алгоритм для р/с и к/с: ключ из 23 цифр (3 служебные
+ 20 самого счёта), веса `7,1,3` по кругу, сумма кратна 10. Для р/с служебная
часть — последние 3 цифры БИК; для к/с — `"0" + цифры 5-6 БИК`.
"""

from __future__ import annotations

from dataclasses import dataclass

from db.models import ClientBankDetails
from db.repositories import get_bank_details, list_client_briefs, upsert_bank_details
from sqlalchemy.ext.asyncio import AsyncSession

from services.brief_parser import BriefValidationError, BriefVariant, parse_brief

_MAX_LEN = 255
_WEIGHTS = [7, 1, 3] * 8


def _checksum_ok(key23: str) -> bool:
    """Контрольная сумма ЦБ: ключ из 23 цифр, веса 7,1,3 по кругу, сумма кратна 10."""
    return sum(int(d) * w for d, w in zip(key23, _WEIGHTS, strict=False)) % 10 == 0


@dataclass(frozen=True)
class BankDetailsInput:
    payer_name: str
    bank_name: str
    bik: str
    settlement_account: str
    correspondent_account: str


class BankDetailsValidationError(Exception):
    """Реквизиты не прошли валидацию. `errors` — поле → код ошибки."""

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__(f"Bank details validation failed: {errors}")


def _validate_name(value: str, field: str, errors: dict[str, str]) -> str:
    """Наименование (плательщик/банк): непустое, до 255 символов."""
    cleaned = value.strip()
    if not cleaned:
        errors[field] = "required"
    elif len(cleaned) > _MAX_LEN:
        errors[field] = "too_long"
    return cleaned


def validate_bank_details(data: BankDetailsInput) -> BankDetailsInput:
    """Очистить пробелы по краям и проверить формат/чек-суммы.

    Бросает `BankDetailsValidationError` со всеми найденными ошибками разом
    (не останавливается на первой) — чтобы форма могла подсветить все поля сразу.
    """
    errors: dict[str, str] = {}

    payer_name = _validate_name(data.payer_name, "payer_name", errors)
    bank_name = _validate_name(data.bank_name, "bank_name", errors)
    bik = data.bik.strip()
    settlement_account = data.settlement_account.strip()
    correspondent_account = data.correspondent_account.strip()

    bik_ok = bool(bik)
    if not bik:
        errors["bik"] = "required"
    elif not (len(bik) == 9 and bik.isdigit()):
        errors["bik"] = "bik_format"
        bik_ok = False

    if not settlement_account:
        errors["settlement_account"] = "required"
    elif not (len(settlement_account) == 20 and settlement_account.isdigit()):
        errors["settlement_account"] = "account_format"
    elif bik_ok and not _checksum_ok(bik[-3:] + settlement_account):
        errors["settlement_account"] = "account_checksum"

    if not correspondent_account:
        errors["correspondent_account"] = "required"
    elif not (
        len(correspondent_account) == 20
        and correspondent_account.isdigit()
        and correspondent_account.startswith("30101")
    ):
        errors["correspondent_account"] = "corr_format"
    elif bik_ok and not _checksum_ok("0" + bik[4:6] + correspondent_account):
        errors["correspondent_account"] = "corr_checksum"

    if errors:
        raise BankDetailsValidationError(errors)

    return BankDetailsInput(
        payer_name=payer_name,
        bank_name=bank_name,
        bik=bik,
        settlement_account=settlement_account,
        correspondent_account=correspondent_account,
    )


async def get_client_bank_details(
    session: AsyncSession, account_id: int, client_id: int
) -> ClientBankDetails | None:
    """Сохранённые реквизиты клиента (или `None`, если ещё не заполнены)."""
    return await get_bank_details(session, account_id, client_id)


async def save_client_bank_details(
    session: AsyncSession, account_id: int, client_id: int, data: BankDetailsInput
) -> ClientBankDetails:
    """Провалидировать и сохранить реквизиты (создать или заменить существующие)."""
    validated = validate_bank_details(data)
    return await upsert_bank_details(
        session,
        account_id,
        client_id,
        payer_name=validated.payer_name,
        bank_name=validated.bank_name,
        bik=validated.bik,
        settlement_account=validated.settlement_account,
        correspondent_account=validated.correspondent_account,
    )


async def latest_brief_bank_hint(
    session: AsyncSession, account_id: int, client_id: int
) -> str | None:
    """Сырой текст реквизитов из последнего брифа клиента — только подсказка.

    `require_tax_id=False` — тот же приём, что `services/launch_service.py`
    использует для уже сохранённых брифов: среди них есть такие, где часть полей
    не спрашивалась, и это не должно ломать подсказку. Ошибка разбора (бриф без
    контактов и т.п.) — не повод падать, просто нет подсказки.
    """
    briefs = await list_client_briefs(session, account_id, client_id)
    if not briefs:
        return None
    latest = briefs[0]
    try:
        parsed = parse_brief(latest.payload, BriefVariant(latest.variant), require_tax_id=False)
    except BriefValidationError:
        return None
    return parsed.bank_details
