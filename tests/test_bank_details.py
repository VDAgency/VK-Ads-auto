"""Тесты валидации банковских реквизитов клиента (`services/bank_details.py`, spec §E)."""

from __future__ import annotations

import pytest
from services.bank_details import (
    BankDetailsInput,
    BankDetailsValidationError,
    validate_bank_details,
)

_WEIGHTS = [7, 1, 3] * 8


def _checksum_ok(key23: str) -> bool:
    return sum(int(d) * w for d, w in zip(key23, _WEIGHTS, strict=False)) % 10 == 0


def _with_valid_control(bik: str, account: str) -> str:
    """Подобрать контрольный разряд (9-й, индекс 8) р/с под БИК — для фикстур."""
    for digit in "0123456789":
        candidate = account[:8] + digit + account[9:]
        if _checksum_ok(bik[-3:] + candidate):
            return candidate
    raise AssertionError("no control digit")


BIK = "044525225"  # Сбербанк, Москва
CORR = "30101810400000000225"  # его к/с — общеизвестный валидный
ACCOUNT = _with_valid_control(BIK, "40702810000000012345")


def _valid_input(**overrides: str) -> BankDetailsInput:
    data = {
        "payer_name": "ИП Иванов Иван Иванович",
        "bank_name": "ПАО Сбербанк",
        "bik": BIK,
        "settlement_account": ACCOUNT,
        "correspondent_account": CORR,
    }
    data.update(overrides)
    return BankDetailsInput(**data)


def test_valid_details_pass() -> None:
    result = validate_bank_details(_valid_input())
    assert result.bik == BIK
    assert result.settlement_account == ACCOUNT
    assert result.correspondent_account == CORR


def test_whitespace_is_trimmed() -> None:
    data = _valid_input(
        payer_name=f"  {_valid_input().payer_name}  ",
        bank_name=f"  {_valid_input().bank_name}\t",
        bik=f" {BIK} ",
        settlement_account=f" {ACCOUNT} ",
        correspondent_account=f" {CORR} ",
    )
    result = validate_bank_details(data)
    assert result.payer_name == "ИП Иванов Иван Иванович"
    assert result.bank_name == "ПАО Сбербанк"
    assert result.bik == BIK
    assert result.settlement_account == ACCOUNT
    assert result.correspondent_account == CORR


def test_bik_wrong_length_rejected() -> None:
    with pytest.raises(BankDetailsValidationError) as exc_info:
        validate_bank_details(_valid_input(bik=BIK[:8]))
    assert exc_info.value.errors["bik"] == "bik_format"


def test_settlement_account_wrong_length_rejected() -> None:
    with pytest.raises(BankDetailsValidationError) as exc_info:
        validate_bank_details(_valid_input(settlement_account=ACCOUNT[:19]))
    assert exc_info.value.errors["settlement_account"] == "account_format"


def test_settlement_account_bad_checksum_rejected() -> None:
    bad = ACCOUNT[:-1] + str((int(ACCOUNT[-1]) + 1) % 10)
    with pytest.raises(BankDetailsValidationError) as exc_info:
        validate_bank_details(_valid_input(settlement_account=bad))
    assert exc_info.value.errors["settlement_account"] == "account_checksum"


def test_correspondent_account_not_starting_with_30101_rejected() -> None:
    bad = "40101" + CORR[5:]
    with pytest.raises(BankDetailsValidationError) as exc_info:
        validate_bank_details(_valid_input(correspondent_account=bad))
    assert exc_info.value.errors["correspondent_account"] == "corr_format"


def test_correspondent_account_bad_checksum_rejected() -> None:
    bad = CORR[:-1] + str((int(CORR[-1]) + 1) % 10)
    with pytest.raises(BankDetailsValidationError) as exc_info:
        validate_bank_details(_valid_input(correspondent_account=bad))
    assert exc_info.value.errors["correspondent_account"] == "corr_checksum"


def test_empty_payer_name_rejected() -> None:
    with pytest.raises(BankDetailsValidationError) as exc_info:
        validate_bank_details(_valid_input(payer_name="   "))
    assert exc_info.value.errors["payer_name"] == "required"


def test_too_long_payer_name_rejected() -> None:
    with pytest.raises(BankDetailsValidationError) as exc_info:
        validate_bank_details(_valid_input(payer_name="а" * 256))
    assert exc_info.value.errors["payer_name"] == "too_long"


def test_multiple_errors_reported_together() -> None:
    with pytest.raises(BankDetailsValidationError) as exc_info:
        validate_bank_details(
            _valid_input(payer_name="", bik=BIK[:8], correspondent_account="40101" + CORR[5:])
        )
    errors = exc_info.value.errors
    assert errors["payer_name"] == "required"
    assert errors["bik"] == "bik_format"
    assert errors["correspondent_account"] == "corr_format"
