"""SessionStore — round-trip шифрования и отказ при неверном ключе (spec §6, §10)."""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken
from userbot.session import SessionStore


def _key() -> str:
    return Fernet.generate_key().decode("ascii")


def test_round_trip_save_then_load(tmp_path: Path) -> None:
    key = _key()
    path = tmp_path / "sub" / "anastasia.session.enc"
    store = SessionStore(key, str(path))

    store.save("1AbCdEf-string-session")

    assert store.load() == "1AbCdEf-string-session"


def test_load_missing_file_returns_none(tmp_path: Path) -> None:
    store = SessionStore(_key(), str(tmp_path / "absent.enc"))
    assert store.load() is None
    assert store.exists() is False


def test_saved_file_is_encrypted_not_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "s.enc"
    SessionStore(_key(), str(path)).save("secret-session-value")
    raw = path.read_bytes()
    assert b"secret-session-value" not in raw


def test_wrong_key_cannot_decrypt(tmp_path: Path) -> None:
    path = tmp_path / "s.enc"
    SessionStore(_key(), str(path)).save("session-data")

    other = SessionStore(_key(), str(path))
    with pytest.raises(InvalidToken):
        other.load()


def test_save_overwrites_previous_session(tmp_path: Path) -> None:
    path = tmp_path / "s.enc"
    store = SessionStore(_key(), str(path))
    store.save("first")
    store.save("second")
    assert store.load() == "second"


def test_invalid_key_rejected_on_construction(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SessionStore("not-a-valid-fernet-key", str(tmp_path / "s.enc"))
