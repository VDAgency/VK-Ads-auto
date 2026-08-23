"""Тесты мок-гейта (§7 spec 2026-07-15 + дефект 2 задачи 2): явный флаг + три условия.

Демо не должны всплывать сами по себе (напр. на приёмке): без явного
`mock_stats_enabled=True` гейт закрыт всегда, что бы ни говорили остальные условия.
"""

from __future__ import annotations

from datetime import UTC, datetime

from services.mock_gate import mock_enabled

_UNTIL = "2026-12-31"
_BEFORE = datetime(2026, 6, 1, tzinfo=UTC)
_AFTER = datetime(2027, 1, 1, tzinfo=UTC)


# --- дефект 2: без явного флага гейт закрыт, даже если остальные условия открыты ---


def test_closed_by_default_when_flag_is_off_even_if_other_conditions_met() -> None:
    assert not mock_enabled(
        real_count=0,
        clients_count=0,
        mock_until=_UNTIL,
        mock_max_clients=5,
        mock_stats_enabled=False,
        now=_BEFORE,
    )


def test_open_when_flag_enabled_and_all_other_conditions_met() -> None:
    assert mock_enabled(
        real_count=0,
        clients_count=0,
        mock_until=_UNTIL,
        mock_max_clients=5,
        mock_stats_enabled=True,
        now=_BEFORE,
    )


# --- остальные условия §7 (проверяются только при включённом флаге) ---------------


def test_open_when_all_conditions_met() -> None:
    assert mock_enabled(
        real_count=0,
        clients_count=0,
        mock_until=_UNTIL,
        mock_max_clients=5,
        mock_stats_enabled=True,
        now=_BEFORE,
    )


def test_closed_when_real_data_exists() -> None:
    assert not mock_enabled(
        real_count=1,
        clients_count=0,
        mock_until=_UNTIL,
        mock_max_clients=5,
        mock_stats_enabled=True,
        now=_BEFORE,
    )


def test_closed_when_deadline_passed() -> None:
    assert not mock_enabled(
        real_count=0,
        clients_count=0,
        mock_until=_UNTIL,
        mock_max_clients=5,
        mock_stats_enabled=True,
        now=_AFTER,
    )


def test_closed_when_client_threshold_reached() -> None:
    assert not mock_enabled(
        real_count=0,
        clients_count=5,
        mock_until=_UNTIL,
        mock_max_clients=5,
        mock_stats_enabled=True,
        now=_BEFORE,
    )


def test_open_just_below_threshold() -> None:
    assert mock_enabled(
        real_count=0,
        clients_count=4,
        mock_until=_UNTIL,
        mock_max_clients=5,
        mock_stats_enabled=True,
        now=_BEFORE,
    )
