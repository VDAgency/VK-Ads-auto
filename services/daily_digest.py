"""Ежедневная сводка оператору о статистике активных кампаний (09:00 МСК, задача 3А).

Синк переиспользует существующий часовой путь (`services.stats_sync.
sync_campaign_stats`) — тот же самый, что дёргает `n8n/workflows/
stats_sync_hourly.json`, просто вызванный ещё раз перед сборкой отчёта, чтобы
цифры к 9 утра были свежими. Метрики по кампании — ПОСЛЕДНИЙ срез (не сумма
истории: `integrations/vk_api.py::get_stats` отдаёт накопительный итог с начала
кампании, суммирование задвоило бы его — тот же приём, что и агрегат кабинета,
`db.repositories.aggregate_cabinet_stats`, вызванный на ОДНОЙ кампании).

Заголовок кампании — `Campaign.spec_json["name"]`: раскладка брифа уже собрала
его как «<цель> · <ФИО клиента>» (`services.mapping.build_campaign_spec`), тут
он просто читается из сохранённой спеки, а не пересобирается заново.

`stale=True` — синк ИМЕННО этой кампании в сегодняшнем прогоне не завершился
успехом (`error`/`skipped` — площадка не ответила, либо кабинет кампании нельзя
определить однозначно). Показанные цифры в этом случае — последний известный
срез, который мог не обновиться сегодня; честно помечаем, а не имитируем
свежесть (CLAUDE.md §7).

Расписание (09:00 МСК) подключается n8n (`n8n/workflows/daily_digest.json`),
здесь только сбор данных и рендер текста. Отправка оператору — через
`services.notifier.notify_operator` (тот же транспорт, что и остальные
уведомления ядра); сам текст — plain text, без HTML/Markdown-разметки, потому
что `notify_operator` шлёт сообщение без `parse_mode`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from config.settings import Settings
from db.models import Campaign
from db.repositories import aggregate_cabinet_stats, get_ad_account, list_active_campaigns
from sqlalchemy.ext.asyncio import AsyncSession

from services.stats_sync import sync_campaign_stats

# Москва — фиксированный UTC+3 без перехода на летнее время (РФ отменила его в
# 2014 году повсеместно): считать дату кабинетным `zoneinfo.ZoneInfo` не нужно,
# а на Windows-окружении разработки его и вовсе может не быть без пакета tzdata.
MOSCOW_TZ = timezone(timedelta(hours=3))


@dataclass(frozen=True)
class DigestRow:
    """Строка отчёта — одна активная кампания."""

    title: str
    cabinet: str
    status: str
    shows: float
    clicks: float
    spent: float
    results: float
    stale: bool

    @property
    def ctr(self) -> float:
        return round(self.clicks / self.shows * 100, 2) if self.shows else 0.0

    @property
    def cpc(self) -> float:
        return round(self.spent / self.clicks, 2) if self.clicks else 0.0

    @property
    def cpl(self) -> float:
        return round(self.spent / self.results, 2) if self.results else 0.0


@dataclass(frozen=True)
class DigestReport:
    """Итог сборки: строки отчёта + дата (по Москве), на которую он собран."""

    rows: list[DigestRow]
    day: date


def _campaign_title(campaign: Campaign) -> str:
    """Заголовок кампании из уже сохранённой спеки; запасной вариант — id."""
    name = campaign.spec_json.get("name") if campaign.spec_json else None
    return name if isinstance(name, str) and name else f"Кампания №{campaign.id}"


async def _cabinet_title(session: AsyncSession, account_id: int, campaign: Campaign) -> str:
    """Название рекламного кабинета кампании; «—» — легаси/заглушка без кабинета."""
    if campaign.ad_account_id is None:
        return "—"
    ad_account = await get_ad_account(session, account_id, campaign.ad_account_id)
    return ad_account.title if ad_account is not None else "—"


async def collect_digest(
    session: AsyncSession, account_id: int, settings: Settings
) -> DigestReport:
    """Синхронизировать активные кампании тенанта и собрать по ним данные отчёта.

    Использует один и тот же `sync_campaign_stats`, что и часовой n8n-синк — не
    отдельный путь опроса площадок. Кампании берутся ДО синка: тот же объект
    `Campaign` мутируется синком в identity-map той же сессии (`db.repositories.
    set_campaign_status`), поэтому `campaign.status` в строке отчёта уже
    отражает результат сегодняшнего прогона, а не устаревшее значение.
    """
    campaigns = await list_active_campaigns(session, account_id)
    sync_summary = await sync_campaign_stats(session, account_id, settings=settings)

    rows: list[DigestRow] = []
    for campaign in campaigns:
        agg = await aggregate_cabinet_stats(session, account_id, campaign.external_id or "")
        cabinet = await _cabinet_title(session, account_id, campaign)
        rows.append(
            DigestRow(
                title=_campaign_title(campaign),
                cabinet=cabinet,
                status=campaign.status,
                shows=agg["shows"],
                clicks=agg["clicks"],
                spent=agg["spent"],
                results=agg["results"],
                stale=sync_summary.get(campaign.id) != "ok",
            )
        )
    return DigestReport(rows=rows, day=datetime.now(MOSCOW_TZ).date())


def _format_money(value: float) -> str:
    return f"{value:.0f} ₽"


def _format_ratio(value: float, has_denominator: bool, suffix: str) -> str:
    return f"{value}{suffix}" if has_denominator else "—"


def render_digest(report: DigestReport) -> str:
    """Собрать текст сводки — plain text (`notify_operator` шлёт без `parse_mode`)."""
    day_label = report.day.strftime("%d.%m.%Y")
    if not report.rows:
        return f"Ежедневная сводка за {day_label}.\n\nАктивных кампаний нет."

    by_cabinet: dict[str, list[DigestRow]] = {}
    for row in report.rows:
        by_cabinet.setdefault(row.cabinet, []).append(row)

    lines = [f"Ежедневная сводка за {day_label}."]
    total_spent = 0.0
    total_results = 0.0
    for cabinet, rows in by_cabinet.items():
        lines.append("")
        lines.append(f"Кабинет «{cabinet}»:")
        for row in rows:
            stale_mark = " (данные не обновились)" if row.stale else ""
            cpc = _format_ratio(row.cpc, bool(row.clicks), " ₽")
            cpl = _format_ratio(row.cpl, bool(row.results), " ₽")
            lines.append(
                f"• {row.title} [{row.status}]{stale_mark}: "
                f"показы {int(row.shows)}, клики {int(row.clicks)}, "
                f"расход {_format_money(row.spent)}, результаты {int(row.results)}, "
                f"CTR {row.ctr}%, CPC {cpc}, CPL {cpl}"
            )
            total_spent += row.spent
            total_results += row.results

    lines.append("")
    lines.append(f"Итого: расход {_format_money(total_spent)}, результатов {int(total_results)}.")
    return "\n".join(lines)
