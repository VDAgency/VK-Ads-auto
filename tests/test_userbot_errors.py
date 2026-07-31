"""Классификация исключений Telethon: код §9, класс ошибки и судьба сессии.

Главное, что здесь закрепляется: что ретраить, а что нет. Ошибка получателя не должна
хоронить сессию, а отозванный ключ не должен выглядеть как временный сетевой сбой.
"""

from __future__ import annotations

import pytest
from telethon import errors
from userbot.errors import ErrorClass, classify, map_send_error, state_for
from userbot.state import SessionState


def test_username_not_occupied() -> None:
    assert map_send_error(errors.UsernameNotOccupiedError(request=None)) == "username_not_occupied"


def test_username_invalid() -> None:
    assert map_send_error(errors.UsernameInvalidError(request=None)) == "username_invalid"


def test_privacy_restricted() -> None:
    assert map_send_error(errors.UserPrivacyRestrictedError(request=None)) == "privacy_restricted"


def test_peer_flood() -> None:
    assert map_send_error(errors.PeerFloodError(request=None)) == "peer_flood"


def test_auth_key_unregistered_maps_to_session_expired() -> None:
    assert map_send_error(errors.AuthKeyUnregisteredError(request=None)) == "session_expired"


def test_unknown_error_falls_back_to_unreachable() -> None:
    assert map_send_error(RuntimeError("boom")) == "userbot_unreachable"


# --- классы ошибок -------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "expected_class", "expected_code"),
    [
        (
            errors.UsernameNotOccupiedError(request=None),
            ErrorClass.RECIPIENT,
            "username_not_occupied",
        ),
        (errors.PeerFloodError(request=None), ErrorClass.RECIPIENT, "peer_flood"),
        (errors.AuthKeyUnregisteredError(request=None), ErrorClass.AUTH_DEAD, "session_expired"),
        (errors.UserDeactivatedBanError(request=None), ErrorClass.ACCOUNT, "account_banned"),
        (ConnectionError("нет связи"), ErrorClass.NETWORK, "userbot_unreachable"),
        (TimeoutError(), ErrorClass.NETWORK, "userbot_unreachable"),
        (OSError("сеть"), ErrorClass.NETWORK, "userbot_unreachable"),
    ],
)
def test_classify_table(exc: BaseException, expected_class: ErrorClass, expected_code: str) -> None:
    assert classify(exc) == (expected_class, expected_code)


def test_duplicated_key_is_auth_dead_not_network() -> None:
    """`AuthKeyDuplicatedError` наследует не `UnauthorizedError` — легко проглядеть."""
    error_class, code = classify(errors.AuthKeyDuplicatedError(request=None))
    assert error_class is ErrorClass.AUTH_DEAD
    assert code == "session_duplicated"


def test_flood_wait_does_not_kill_the_session() -> None:
    """Лимит на отправку — про получателя и темп, а не про живость сессии."""
    error_class, code = classify(errors.FloodWaitError(request=None))
    assert error_class is ErrorClass.RECIPIENT
    assert code == "flood_wait"
    assert state_for(error_class) is None


@pytest.mark.parametrize(
    ("error_class", "expected"),
    [
        (ErrorClass.NETWORK, SessionState.UNREACHABLE),
        (ErrorClass.AUTH_DEAD, SessionState.EXPIRED),
        (ErrorClass.ACCOUNT, SessionState.BANNED),
        (ErrorClass.RECIPIENT, None),
    ],
)
def test_state_for_class(error_class: ErrorClass, expected: SessionState | None) -> None:
    assert state_for(error_class) is expected


def test_unknown_exception_is_treated_as_network() -> None:
    """Незнакомое лучше перепробовать, чем похоронить живую сессию."""
    assert classify(RuntimeError("что-то новое"))[0] is ErrorClass.NETWORK
