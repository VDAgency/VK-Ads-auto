"""Экран `/userbot_status`: рендер состояний и маскировка номера."""

from __future__ import annotations

import pytest
from services.userbot_report import SessionReport, mask_phone, render_status

_READY = SessionReport(sender_id=111, authorized=True, unreachable=False, phone="79871658054")
_UNREACHABLE = SessionReport(sender_id=222, authorized=False, unreachable=True)
_DEAD = SessionReport(sender_id=333, authorized=False, unreachable=False)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("79871658054", "+7987…054"),
        ("+7 987 165-80-54", "+7987…054"),
        (None, "—"),
        ("", "—"),
        ("12345", "—"),
    ],
)
def test_mask_phone(raw: str | None, expected: str) -> None:
    assert mask_phone(raw) == expected


def test_full_phone_never_leaks_to_screen() -> None:
    """Экран остаётся в истории чата — полный номер там не нужен."""
    text = render_status(service_up=True, proxy_configured=False, sessions=[_READY])
    assert "79871658054" not in text
    assert "+7987…054" in text


def test_ready_session_is_marked_working() -> None:
    text = render_status(service_up=True, proxy_configured=False, sessions=[_READY])
    assert "✅ работает" in text


def test_unreachable_session_does_not_advise_relinking() -> None:
    """Перепривязка при сетевой блокировке бесполезна — совет был бы вредным."""
    text = render_status(service_up=True, proxy_configured=False, sessions=[_UNREACHABLE])
    assert "Telegram недоступен" in text
    assert "/link_userbot" not in text


def test_dead_session_advises_relinking() -> None:
    text = render_status(service_up=True, proxy_configured=False, sessions=[_DEAD])
    assert "/link_userbot" in text


def test_service_down_hides_session_details() -> None:
    text = render_status(service_up=False, proxy_configured=False, sessions=[_READY])
    assert "не отвечает" in text
    assert "состояние сессий неизвестно" in text


def test_unknown_service_state_is_honest() -> None:
    text = render_status(service_up=None, proxy_configured=False, sessions=[])
    assert "ещё не опрашивали" in text


def test_no_sessions_points_to_linking() -> None:
    text = render_status(service_up=True, proxy_configured=False, sessions=[])
    assert "/link_userbot" in text


def test_foreign_session_is_flagged() -> None:
    """Сессия не из списка операторов показывается, но помечается."""
    foreign = SessionReport(sender_id=444, authorized=True, unreachable=False, is_operator=False)
    text = render_status(service_up=True, proxy_configured=False, sessions=[foreign])
    assert "не в списке операторов" in text


def test_proxy_state_is_shown() -> None:
    assert "Прокси: задан" in render_status(
        service_up=True, proxy_configured=True, sessions=[_READY]
    )
    assert "Прокси: не задан" in render_status(
        service_up=True, proxy_configured=False, sessions=[_READY]
    )


def test_email_hint_when_something_is_unreachable() -> None:
    text = render_status(service_up=True, proxy_configured=False, sessions=[_READY, _UNREACHABLE])
    assert "email" in text
