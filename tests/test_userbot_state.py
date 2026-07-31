"""Реестр состояний сессий: переходы, откат и терминальные состояния."""

from __future__ import annotations

from userbot.state import TERMINAL_STATES, SessionState, StateRegistry


def test_unknown_session_starts_as_unknown() -> None:
    assert StateRegistry().get(111).state is SessionState.UNKNOWN


def test_mark_ok_fills_details_and_resets_failures() -> None:
    registry = StateRegistry()
    registry.mark_failed(111, state=SessionState.UNREACHABLE, error="userbot_unreachable", now=0.0)
    info = registry.mark_ok(111, phone="79990001122", endpoint="dc2 …:443", now=100.0)

    assert info.state is SessionState.READY
    assert info.phone == "79990001122"
    assert info.last_ok_at == 100.0
    assert info.last_error is None
    assert info.consecutive_failures == 0
    assert info.next_attempt_at == 0.0


def test_backoff_grows_with_each_failure() -> None:
    """Мёртвый дата-центр не нужно дёргать с прежней частотой."""
    registry = StateRegistry()
    delays = []
    for attempt in range(4):
        info = registry.mark_failed(
            111,
            state=SessionState.UNREACHABLE,
            error="userbot_unreachable",
            now=0.0,
            backoff_base=300.0,
        )
        delays.append(info.next_attempt_at)
        assert info.consecutive_failures == attempt + 1
    assert delays == [300.0, 600.0, 1200.0, 2400.0]


def test_backoff_is_capped() -> None:
    registry = StateRegistry()
    for _ in range(20):
        info = registry.mark_failed(
            111,
            state=SessionState.UNREACHABLE,
            error="userbot_unreachable",
            now=0.0,
            backoff_base=300.0,
            backoff_cap=3600.0,
        )
    assert info.next_attempt_at == 3600.0


def test_terminal_states_are_never_retried() -> None:
    """Ретрай с отозванным ключом бесполезен, а Telegram реагирует на него хуже."""
    registry = StateRegistry()
    for state in TERMINAL_STATES:
        info = registry.mark_failed(111, state=state, error="session_expired", now=0.0)
        assert info.next_attempt_at == float("inf")
        assert registry.due(111, now=10**9) is False


def test_due_respects_backoff() -> None:
    registry = StateRegistry()
    registry.mark_failed(
        111, state=SessionState.UNREACHABLE, error="x", now=0.0, backoff_base=300.0
    )
    assert registry.due(111, now=299.0) is False
    assert registry.due(111, now=300.0) is True


def test_snapshot_is_sorted_and_complete() -> None:
    registry = StateRegistry()
    registry.get(222)
    registry.get(111)
    assert [info.sender_id for info in registry.snapshot()] == [111, 222]


def test_forget_removes_session() -> None:
    registry = StateRegistry()
    registry.mark_ok(111, phone=None, endpoint=None, now=0.0)
    registry.forget(111)
    assert registry.get(111).state is SessionState.UNKNOWN


def test_as_dict_keeps_authorized_flag() -> None:
    """`authorized` остаётся в ответе: на него смотрят существующие клиенты."""
    registry = StateRegistry()
    ready = registry.mark_ok(111, phone="79990001122", endpoint="dc2", now=5.0).as_dict()
    assert ready["authorized"] is True
    assert ready["state"] == "ready"

    failed = registry.mark_failed(
        222, state=SessionState.UNREACHABLE, error="userbot_unreachable", now=5.0
    ).as_dict()
    assert failed["authorized"] is False
    assert failed["state"] == "unreachable"
    assert failed["error"] == "userbot_unreachable"
