"""Ротация резервных копий БД (infra/backup/rotate.py).

Проверяем только логику ротации — без Docker и без живой БД (задача 3,
docs/ROADMAP.md), чтобы тест надёжно гонялся на Windows-машине разработчика.
Сам pg_dump и планировщик (infra/backup/backup.sh, entrypoint.sh) руками не
воспроизвести без Docker, поэтому за пределами теста — только ревью скриптов.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from infra.backup.rotate import rotate_backups


def _make_backup(path: Path, mtime: float) -> None:
    """Создаёт файл-заглушку дампа с заданным временем изменения."""
    path.write_bytes(b"fake dump content")
    os.utime(path, (mtime, mtime))


def test_keeps_only_n_newest_of_n_plus_five_files(tmp_path: Path) -> None:
    """N+5 файлов с разными датами -> после ротации остаётся ровно N самых свежих."""
    keep = 7
    total = keep + 5
    base_time = time.time() - total * 60  # разносим mtime на минуту, самый старый — первый

    names_by_age = [f"vk_ads_auto-{i:02d}.sql.gz" for i in range(total)]
    for i, name in enumerate(names_by_age):
        _make_backup(tmp_path / name, base_time + i * 60)

    result = rotate_backups(tmp_path, keep=keep)

    remaining = sorted(p.name for p in tmp_path.glob("*.sql.gz"))
    expected_remaining = sorted(names_by_age[-keep:])  # keep newest `keep` by mtime
    assert remaining == expected_remaining
    assert len(result.kept) == keep
    assert len(result.deleted) == total - keep
    assert {p.name for p in result.deleted} == set(names_by_age[: total - keep])


def test_empty_directory_is_not_an_error(tmp_path: Path) -> None:
    result = rotate_backups(tmp_path, keep=14)

    assert result.kept == []
    assert result.deleted == []


def test_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    missing_dir = tmp_path / "does-not-exist-yet"

    result = rotate_backups(missing_dir, keep=14)

    assert result.kept == []
    assert result.deleted == []


def test_fewer_files_than_limit_keeps_all_of_them(tmp_path: Path) -> None:
    for i in range(3):
        _make_backup(tmp_path / f"vk_ads_auto-{i}.sql.gz", time.time() + i)

    result = rotate_backups(tmp_path, keep=14)

    assert len(result.kept) == 3
    assert result.deleted == []
    assert len(list(tmp_path.glob("*.sql.gz"))) == 3


def test_files_not_matching_pattern_are_left_untouched(tmp_path: Path) -> None:
    (tmp_path / "readme.txt").write_bytes(b"not a backup dump")
    for i in range(3):
        _make_backup(tmp_path / f"vk_ads_auto-{i}.sql.gz", time.time() + i)

    result = rotate_backups(tmp_path, keep=1)

    assert (tmp_path / "readme.txt").exists()
    assert len(result.deleted) == 2


def test_rejects_negative_keep_count(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError):
        rotate_backups(tmp_path, keep=-1)
