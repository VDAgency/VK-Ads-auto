"""CLI: удалить тестовые кампании тенанта — разовая операция обслуживания.

НЕ команда бота (CLAUDE.md §5.2): вызывается оператором вручную внутри
контейнера, не является функцией продукта. Вся логика — в
`services.campaign_cleanup`; здесь только ввод/вывод.

Запуск (внутри контейнера `api`/`bot`, где есть доступ к БД и настройкам):

    python -m scripts.cleanup_test_campaigns
    python -m scripts.cleanup_test_campaigns --name-contains тест --exclude 42
    python -m scripts.cleanup_test_campaigns --execute

Без `--execute` — только сухой прогон: список кандидатов, никаких изменений.
`--execute` реально удаляет кампании на площадке (адаптер боевого кабинета по
умолчанию) и в БД; без активного и однозначного рекламного кабинета тенанта
реальное удаление честно отказывает — без него неоткуда взять токен VK.

`--name-contains` проверяется `services.campaign_cleanup.validate_name_contains`
ещё на этапе разбора аргументов: пустая строка, строка из пробелов или критерий
короче нескольких символов отклоняются сразу, до открытия сессии БД — та же
защита, что стоит и в самом сервисе (см. его docstring), здесь только раньше.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from db.session import get_sessionmaker
from integrations.adapter import PlatformAdapter
from integrations.vk_api import VkApiAdapter
from services.ad_accounts import AdAccountError, resolve_default_account
from services.campaign_cleanup import (
    DEFAULT_NAME_MARKER,
    CleanupSummary,
    cleanup_test_campaigns,
    validate_name_contains,
)
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Единственный тенант проекта на сейчас — тот же дефолт, что у `/api/v1/campaigns`
# (core/api/v1/campaigns.py::DEFAULT_ACCOUNT_ID).
DEFAULT_ACCOUNT_ID = 1


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Удаление тестовых кампаний тенанта (боевой кабинет VK + БД).",
    )
    parser.add_argument(
        "--name-contains",
        type=validate_name_contains,
        default=DEFAULT_NAME_MARKER,
        help=(
            f"подстрока в имени кампании, регистронезависимо (по умолчанию "
            f"{DEFAULT_NAME_MARKER!r}); пустая, из пробелов или слишком короткая строка "
            "отклоняется — иначе критерий совпадёт практически с любой кампанией тенанта"
        ),
    )
    parser.add_argument(
        "--exclude",
        type=int,
        nargs="*",
        default=[],
        metavar="ID",
        help="id кампаний, которые нужно сохранить, даже если они подходят под критерий",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="реально удалить (площадка + БД); без флага — только показать список кандидатов",
    )
    parser.add_argument(
        "--account-id", type=int, default=DEFAULT_ACCOUNT_ID, help="id тенанта (обычно не менять)"
    )
    return parser.parse_args(argv)


async def _build_live_adapter(session: AsyncSession, account_id: int) -> PlatformAdapter:
    """Адаптер боевого кабинета по умолчанию — нужен только для реального удаления."""
    _, token = await resolve_default_account(session, account_id)
    return VkApiAdapter(token)


def _print_summary(summary: CleanupSummary) -> None:
    mode = "СУХОЙ ПРОГОН" if summary.dry_run else "РЕАЛЬНОЕ УДАЛЕНИЕ"
    print(f"=== {mode}: под критерий подходит {len(summary.candidates)} кампаний ===")
    for candidate in summary.candidates:
        print(
            f"  id={candidate.campaign_id} status={candidate.status} "
            f"external_id={candidate.external_id!r} name={candidate.brief_name!r}"
        )
    if not summary.candidates:
        print("  (пусто)")

    if summary.dry_run:
        print("\nЭто сухой прогон — ничего не изменено.")
        print("Запустите с --execute, чтобы удалить по-настоящему.")
        return

    print()
    for outcome in summary.outcomes:
        line = f"  id={outcome.campaign_id}: {outcome.result}"
        if outcome.detail:
            line += f" ({outcome.detail})"
        print(line)
    print(
        f"\nИтог: удалено {summary.deleted}, пропущено {summary.skipped}, ошибок {summary.errors}"
    )


async def _run(args: argparse.Namespace) -> None:
    sessionmaker = get_sessionmaker()
    async with sessionmaker() as session:
        adapter: PlatformAdapter | None = None
        if args.execute:
            try:
                adapter = await _build_live_adapter(session, args.account_id)
            except AdAccountError as exc:
                print(f"Не удалось определить рекламный кабинет для реального удаления: {exc}")
                print("Запустите без --execute, чтобы увидеть список кандидатов.")
                return
        summary = await cleanup_test_campaigns(
            session,
            args.account_id,
            adapter=adapter,
            name_contains=args.name_contains,
            exclude_ids=args.exclude,
            dry_run=not args.execute,
        )
        if not summary.dry_run:
            await session.commit()
    _print_summary(summary)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO)
    args = _parse_args(argv)
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
