"""Маппинг исключений Telethon в коды §9 (userbot/errors.py)."""

from telethon import errors
from userbot.errors import map_send_error


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
