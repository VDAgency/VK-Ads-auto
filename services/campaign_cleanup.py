"""Массовая очистка тестовых кампаний тенанта — разовая операция обслуживания.

НЕ функция оператора и НЕ команда бота: вызывается вручную скриптом
`scripts/cleanup_test_campaigns.py` внутри контейнера. Кампания считается
«тестовой», если критерий (по умолчанию подстрока «тест», регистронезависимо)
входит в `Campaign.spec_json["name"]` — это имя уже собрано как
`<цель> · <full_name из брифа>` (`services.mapping.build_campaign_spec`), то
есть имя клиента из брифа туда попадает напрямую.

Правила безопасности (боевой рекламный кабинет реального клиента — цена
ошибочного удаления высокая, CLAUDE.md §7):
- критерий `name_contains` проверяется `validate_name_contains` до похода в БД —
  пустая строка, строка из пробелов и критерий короче `MIN_NAME_CONTAINS_LENGTH`
  символов отклоняются с `ValueError` (иначе они матчат практически любую
  кампанию тенанта, включая боевую — имя кампании почти всегда содержит пробел);
- по умолчанию `dry_run=True` — ничего не меняется, только возвращается список
  кандидатов (id, статус, external_id, имя из брифа);
- `exclude_ids` — явное исключение конкретных id из обработки полностью (даже
  из списка кандидатов сухого прогона);
- кампания с `external_id`, начинающимся на `stub-`, в VK не существует — на
  площадку не уходит вовсе, удаляется только строка в БД;
- ошибка удаления одной кампании не прерывает обработку остальных — попадает в
  сводку как `error` с причиной, остальные кампании обрабатываются дальше;
- удаляются ТОЛЬКО кампании, реально попавшие под критерий — никакого «заодно».

Коммит — на вызывающем (тот же принцип, что у `services.launch_service` и
`services.stats_sync`).
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

from db.models import Campaign
from db.repositories import delete_campaign_row, list_campaigns
from integrations.adapter import PlatformAdapter
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Критерий по умолчанию — подстрока в имени кампании (регистронезависимо).
DEFAULT_NAME_MARKER = "тест"
# Минимальная длина критерия после обрезки пробелов. Имя кампании собирается как
# «<цель> · <ФИО клиента>» (services.mapping.build_campaign_spec) — в нём почти
# всегда есть пробел, поэтому пустая строка и любая совсем короткая подстрока
# матчат практически ЛЮБУЮ кампанию тенанта, включая боевые. Порог небольшой:
# он не должен мешать нормальным критериям («тест», «демо»), только отсекать
# заведомо негодные (пустую строку, один-два случайных символа).
MIN_NAME_CONTAINS_LENGTH = 3
# У кампаний, которые никогда не уходили в VK (созданы на заглушке), external_id
# начинается с этого префикса (см. `integrations.stub.StubAdapter`).
STUB_EXTERNAL_ID_PREFIX = "stub-"

# Возможные исходы обработки одной кампании при реальном удалении.
OK = "ok"
SKIPPED = "skipped"
ERROR = "error"


@dataclass(frozen=True, slots=True)
class CleanupCandidate:
    """Кампания, попавшая под критерий очистки (кандидат на удаление)."""

    campaign_id: int
    status: str
    external_id: str | None
    brief_name: str


@dataclass(frozen=True, slots=True)
class CleanupOutcome:
    """Итог обработки одной кампании при реальном удалении."""

    campaign_id: int
    result: str  # OK | SKIPPED | ERROR
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CleanupSummary:
    """Сводка прогона очистки — то, что показывается оператору."""

    dry_run: bool
    candidates: list[CleanupCandidate] = field(default_factory=list)
    outcomes: list[CleanupOutcome] = field(default_factory=list)

    @property
    def deleted(self) -> int:
        return sum(1 for o in self.outcomes if o.result == OK)

    @property
    def skipped(self) -> int:
        return sum(1 for o in self.outcomes if o.result == SKIPPED)

    @property
    def errors(self) -> int:
        return sum(1 for o in self.outcomes if o.result == ERROR)


def _brief_name(campaign: Campaign) -> str:
    """Имя кампании из спеки (включает имя клиента из брифа); пусто, если спеки нет."""
    spec = campaign.spec_json
    if not isinstance(spec, dict):
        return ""
    name = spec.get("name")
    return str(name) if name else ""


def _matches(campaign: Campaign, name_contains: str) -> bool:
    """Критерий — подстрока в имени кампании, регистронезависимо."""
    return name_contains.lower() in _brief_name(campaign).lower()


def _is_stub(external_id: str | None) -> bool:
    """Кампания создана на заглушке и в VK никогда не существовала."""
    return external_id is not None and external_id.startswith(STUB_EXTERNAL_ID_PREFIX)


def validate_name_contains(name_contains: str) -> str:
    """Проверить критерий очистки ДО похода в БД и в адаптер.

    Пустая строка, строка из одних пробелов или критерий короче
    `MIN_NAME_CONTAINS_LENGTH` символов матчат практически любую кампанию
    тенанта (см. пояснение у константы) — ровно тот сценарий, из-за которого
    опечатка или забытая переменная окружения в команде запуска
    (`--name-contains "$X"` при пустом `$X`) стирает вместе с тестовыми и
    единственную боевую кампанию тенанта.

    Проверка стоит здесь, в сервисе, а не только в разборе аргументов CLI —
    защита обязана срабатывать и при прямом вызове сервиса в коде, в обход
    `scripts/cleanup_test_campaigns.py`. Сигнатура (принимает и возвращает
    строку, кидает `ValueError`) позволяет использовать функцию и как
    `type=` в `argparse` — так CLI получает ту же проверку и понятную ошибку
    ещё на этапе разбора аргументов, до открытия сессии БД.
    """
    stripped = name_contains.strip()
    if not stripped:
        raise ValueError(
            "критерий --name-contains не может быть пустым или состоять из одних пробелов — "
            "такой критерий совпадёт практически с любой кампанией тенанта"
        )
    if len(stripped) < MIN_NAME_CONTAINS_LENGTH:
        raise ValueError(
            f"критерий --name-contains слишком короткий ({name_contains!r}) — "
            f"минимум {MIN_NAME_CONTAINS_LENGTH} символа, иначе он совпадёт "
            "слишком широко"
        )
    return name_contains


async def find_test_campaigns(
    session: AsyncSession,
    account_id: int,
    *,
    name_contains: str = DEFAULT_NAME_MARKER,
    exclude_ids: Iterable[int] = (),
) -> list[CleanupCandidate]:
    """Кампании тенанта, чьё имя из брифа содержит критерий (регистронезависимо).

    `exclude_ids` исключает кампании ПОЛНОСТЬЮ — их не будет и в сухом прогоне
    (владелец хочет их не трогать вообще, а не просто не удалять). Критерий
    проверяется `validate_name_contains` ДО обращения к БД — негодный критерий
    (пустой, из пробелов, слишком короткий) отклоняется с понятной ошибкой.
    """
    validate_name_contains(name_contains)
    excluded = set(exclude_ids)
    campaigns = await list_campaigns(session, account_id)
    return [
        CleanupCandidate(
            campaign_id=campaign.id,
            status=campaign.status,
            external_id=campaign.external_id,
            brief_name=_brief_name(campaign),
        )
        for campaign in campaigns
        if campaign.id not in excluded and _matches(campaign, name_contains)
    ]


async def cleanup_test_campaigns(
    session: AsyncSession,
    account_id: int,
    *,
    adapter: PlatformAdapter | None = None,
    name_contains: str = DEFAULT_NAME_MARKER,
    exclude_ids: Iterable[int] = (),
    dry_run: bool = True,
) -> CleanupSummary:
    """Найти и (если не сухой прогон) удалить тестовые кампании тенанта.

    Критерий проверяется `find_test_campaigns` (через `validate_name_contains`)
    ещё до первого обращения к БД — негодный критерий (пустой, из пробелов,
    слишком короткий) отклоняется здесь же, до похода в адаптер, даже если этот
    сервис вызван напрямую из кода, в обход CLI-скрипта.

    По умолчанию `dry_run=True` — ничего не меняется, сводка несёт только
    список кандидатов. Реальное удаление (`dry_run=False`) требует явный
    `adapter` (иначе `ValueError` — предохранитель от забытого аргумента,
    а не молчаливая деградация до сухого прогона).

    Для каждого кандидата: кампания без `stub-` `external_id` сначала
    удаляется на площадке через `adapter.delete_campaign`, и только при
    успехе — строка в БД (`db.repositories.delete_campaign_row`). `stub-`
    кампании и кампании без `external_id` на площадку не уходят вовсе — там
    удалять нечего, сразу чистится только строка. Ошибка одной кампании
    (площадка отказала, сеть упала) не прерывает обработку остальных — уходит
    в лог и в сводку как `error`; сама кампания и её строка в БД остаются
    нетронутыми, чтобы можно было повторить попытку позже.
    """
    candidates = await find_test_campaigns(
        session, account_id, name_contains=name_contains, exclude_ids=exclude_ids
    )
    if dry_run:
        return CleanupSummary(dry_run=True, candidates=candidates)
    if adapter is None:
        raise ValueError("adapter is required when dry_run=False")

    outcomes: list[CleanupOutcome] = []
    for candidate in candidates:
        try:
            if candidate.external_id and not _is_stub(candidate.external_id):
                await adapter.delete_campaign(candidate.external_id)
            removed = await delete_campaign_row(session, account_id, candidate.campaign_id)
            if removed:
                outcomes.append(CleanupOutcome(candidate.campaign_id, OK))
            else:
                # Строки уже не было к моменту удаления (гонка/повторный прогон) —
                # не ошибка, но и удалять было нечего.
                outcomes.append(
                    CleanupOutcome(candidate.campaign_id, SKIPPED, "campaign row already gone")
                )
        except Exception as exc:  # noqa: BLE001 — одна кампания не должна ронять остальные
            logger.exception("cleanup failed for campaign %s", candidate.campaign_id)
            outcomes.append(CleanupOutcome(candidate.campaign_id, ERROR, str(exc)))
    return CleanupSummary(dry_run=False, candidates=candidates, outcomes=outcomes)
