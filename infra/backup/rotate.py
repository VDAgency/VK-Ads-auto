"""Ротация файлов резервных копий БД.

Логика вынесена из shell-скрипта (infra/backup/backup.sh) в чистый Python без
внешних зависимостей по двум причинам:
  - её нужно юнит-тестировать (tests/test_backup_script.py) на Windows-машине
    разработчика без Docker и без живой БД;
  - сортировка/удаление файлов на чистом POSIX shell — источник трудноуловимых
    ошибок (сравнение имён вместо дат, гонки при пустом/битом glob и т.п.).

backup.sh вызывает этот модуль как обычный скрипт: `python3 rotate.py <dir> <keep>`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

# Формат имени дампа задаёт backup.sh: "<POSTGRES_DB>-<timestamp>.sql.gz".
DEFAULT_PATTERN = "*.sql.gz"


@dataclass
class RotationResult:
    """Что произошло при ротации: какие файлы оставлены, какие удалены."""

    kept: list[Path] = field(default_factory=list)
    deleted: list[Path] = field(default_factory=list)


def rotate_backups(directory: Path, keep: int, pattern: str = DEFAULT_PATTERN) -> RotationResult:
    """Оставляет `keep` самых свежих файлов `pattern` в `directory`, остальные удаляет.

    Свежесть — по времени модификации файла (mtime), а не по разбору имени:
    бэкапы и так появляются по одному в сутки, а mtime не зависит от того,
    как отформатирована метка времени в имени файла.

    Отсутствующий или пустой каталог — не ошибка (например, самый первый
    прогон бэкапа на чистом сервере): результат просто пустой.
    """
    if keep < 0:
        raise ValueError(f"keep не может быть отрицательным: {keep}")

    if not directory.exists():
        return RotationResult()

    candidates = sorted(
        (p for p in directory.glob(pattern) if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )

    kept = candidates[:keep]
    to_delete = candidates[keep:]

    for path in to_delete:
        path.unlink()

    return RotationResult(kept=kept, deleted=to_delete)


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: rotate.py <backup_dir> <keep_count> [pattern]", file=sys.stderr)
        return 2

    directory = Path(argv[0])
    try:
        keep = int(argv[1])
    except ValueError:
        print(f"keep_count должен быть целым числом, получено: {argv[1]!r}", file=sys.stderr)
        return 2

    pattern = argv[2] if len(argv) > 2 else DEFAULT_PATTERN

    try:
        result = rotate_backups(directory, keep, pattern)
    except ValueError as exc:
        print(f"rotate: ОШИБКА: {exc}", file=sys.stderr)
        return 1

    for path in result.deleted:
        print(f"rotate: удалён старый бэкап {path.name}")
    print(f"rotate: хранится {len(result.kept)} копий (лимит {keep}) в {directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
