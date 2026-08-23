"""Тесты CLI-обвязки `scripts/cleanup_test_campaigns.py` (парсинг аргументов и вывод).

Сама логика очистки покрыта `tests/test_campaign_cleanup.py` — здесь только то,
что скрипт добавляет сверху: разбор аргументов и человеко-читаемый вывод.
"""

from __future__ import annotations

import pytest
from scripts.cleanup_test_campaigns import _parse_args, _print_summary
from services.campaign_cleanup import (
    DEFAULT_NAME_MARKER,
    CleanupCandidate,
    CleanupOutcome,
    CleanupSummary,
)


def test_parse_args_defaults() -> None:
    args = _parse_args([])
    assert args.name_contains == DEFAULT_NAME_MARKER
    assert args.exclude == []
    assert args.execute is False


def test_parse_args_execute_and_exclude() -> None:
    args = _parse_args(["--execute", "--exclude", "1", "2", "--name-contains", "демо"])
    assert args.execute is True
    assert args.exclude == [1, 2]
    assert args.name_contains == "демо"


# --- --name-contains: защита от негодного критерия (падает на разборе аргументов,
# до похода в БД) — та же защита, что и в `services.campaign_cleanup`, здесь только
# даёт понятную ошибку раньше и без запуска сессии БД.


def test_parse_args_rejects_empty_name_contains() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--name-contains", ""])


def test_parse_args_rejects_whitespace_only_name_contains() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--name-contains", "   "])


def test_parse_args_rejects_too_short_name_contains() -> None:
    with pytest.raises(SystemExit):
        _parse_args(["--name-contains", "ab"])


def test_print_summary_dry_run_lists_candidates_and_does_not_claim_deletion(
    capsys: pytest.CaptureFixture[str],
) -> None:
    summary = CleanupSummary(
        dry_run=True,
        candidates=[CleanupCandidate(1, "prepared", "100", "ТЕСТ Иванов")],
    )
    _print_summary(summary)
    out = capsys.readouterr().out
    assert "СУХОЙ ПРОГОН" in out
    assert "id=1" in out
    assert "ТЕСТ Иванов" in out
    assert "--execute" in out
    assert "Итог:" not in out


def test_print_summary_real_run_shows_final_counters(capsys: pytest.CaptureFixture[str]) -> None:
    summary = CleanupSummary(
        dry_run=False,
        candidates=[
            CleanupCandidate(1, "prepared", "100", "ТЕСТ 1"),
            CleanupCandidate(2, "prepared", "200", "ТЕСТ 2"),
        ],
        outcomes=[
            CleanupOutcome(1, "ok"),
            CleanupOutcome(2, "error", "boom"),
        ],
    )
    _print_summary(summary)
    out = capsys.readouterr().out
    assert "РЕАЛЬНОЕ УДАЛЕНИЕ" in out
    assert "id=2: error (boom)" in out
    assert "удалено 1, пропущено 0, ошибок 1" in out


def test_print_summary_empty_candidates_says_so(capsys: pytest.CaptureFixture[str]) -> None:
    _print_summary(CleanupSummary(dry_run=True, candidates=[]))
    out = capsys.readouterr().out
    assert "(пусто)" in out
