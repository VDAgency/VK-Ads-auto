"""Рендер экрана диагностики юзербота (`/userbot_status`).

Вся вёрстка — чистые функции без aiogram и без сети: экран проверяется таблицей,
а не поднятым ботом (CLAUDE.md §1.3).

Телефон маскируется всегда: экран остаётся в истории чата, и светить полный номер
оператора там незачем.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionReport:
    """Строка экрана: состояние одной сессии юзербота."""

    sender_id: int
    authorized: bool
    unreachable: bool
    phone: str | None = None
    is_operator: bool = True


def mask_phone(phone: str | None) -> str:
    """Замаскировать номер: `79871658054` → `+7987…054`. Пусто → прочерк."""
    digits = "".join(ch for ch in (phone or "") if ch.isdigit())
    if len(digits) < 7:
        return "—"
    return f"+{digits[:4]}…{digits[-3:]}"


def _session_lines(report: SessionReport) -> list[str]:
    who = f"👤 {report.sender_id}"
    if report.phone:
        who += f" ({mask_phone(report.phone)})"
    if not report.is_operator:
        who += " — не в списке операторов"
    lines = [who]
    if report.authorized:
        lines.append("   ✅ работает")
    elif report.unreachable:
        lines.append("   ⚠️ Telegram недоступен с сервера")
        lines.append("   Перепривязка не поможет — бот продолжает пробовать сам")
    else:
        lines.append("   ⛔ не подключена")
        lines.append("   Подключить: /link_userbot")
    return lines


def render_status(
    *, service_up: bool | None, proxy_configured: bool, sessions: list[SessionReport]
) -> str:
    """Экран `/userbot_status`: состояние сервиса, прокси и всех сессий."""
    if service_up is None:
        service = "… ещё не опрашивали"
    elif service_up:
        service = "✅ доступен"
    else:
        service = "⛔ не отвечает"
    lines = [
        "🤖 <b>Юзербот</b>",
        f"Сервис: {service}",
        f"Прокси: {'задан' if proxy_configured else 'не задан'}",
        "",
    ]
    if service_up is False:
        lines.append("Пока сервис не отвечает, состояние сессий неизвестно.")
        lines.append("Брифы можно отправлять на email.")
        return "\n".join(lines)
    if not sessions:
        lines.append("Ни одной сессии не подключено. Начните с /link_userbot")
        return "\n".join(lines)
    for report in sessions:
        lines.extend(_session_lines(report))
        lines.append("")
    if any(report.unreachable for report in sessions):
        lines.append("Пока Telegram недоступен, брифы уходят на email и вручную.")
    return "\n".join(lines).rstrip()


def render_endpoints(rows: list[dict[str, object]]) -> str:
    """Матрица достижимости точек подключения — для разбора «а куда мы вообще ходим».

    Показывает ровно то, что перебирает сервис: адрес, порт, транспорт и через прокси
    ли. Именно это различие («TCP открыт, но MTProto режется») и оказалось ключевым.
    """
    if not rows:
        return "🧪 <b>Точки подключения</b>\n\nСписок пуст — сессий пока нет."
    lines = ["🧪 <b>Точки подключения</b>", ""]
    for row in rows:
        mark = "✅" if row.get("reachable") else "⛔"
        latency = row.get("latency_ms")
        tail = f" · {latency} мс" if isinstance(latency, int) else ""
        label = f"dc{row.get('dc_id')} {row.get('ip')}:{row.get('port')}"
        lines.append(f"{mark} {label}{tail}")
    reachable = sum(1 for row in rows if row.get("reachable"))
    lines.append("")
    lines.append(f"Напрямую доступно {reachable} из {len(rows)}.")
    lines.append(
        "Это прямая проверка соединения. Адрес может отвечать, а обмен с Telegram — "
        "блокироваться; и наоборот, через прокси работают адреса, недоступные напрямую."
    )
    return "\n".join(lines)
