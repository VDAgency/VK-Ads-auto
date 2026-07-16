"""SessionStore — шифрование по операторам, изоляция, list_senders (spec §6, §10)."""

from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken
from userbot.session import SessionStore


def _key() -> str:
    return Fernet.generate_key().decode("ascii")


def test_round_trip_save_then_load(tmp_path: Path) -> None:
    store = SessionStore(_key(), str(tmp_path / "sub"))

    store.save(111, "1AbCdEf-string-session")

    assert store.load(111) == "1AbCdEf-string-session"


def test_load_missing_sender_returns_none(tmp_path: Path) -> None:
    store = SessionStore(_key(), str(tmp_path))
    assert store.load(111) is None
    assert store.exists(111) is False


def test_two_senders_are_isolated(tmp_path: Path) -> None:
    store = SessionStore(_key(), str(tmp_path))
    store.save(111, "session-of-first")
    store.save(222, "session-of-second")

    assert store.load(111) == "session-of-first"
    assert store.load(222) == "session-of-second"
    assert (tmp_path / "111.session.enc").exists()
    assert (tmp_path / "222.session.enc").exists()


def test_saved_file_is_encrypted_not_plaintext(tmp_path: Path) -> None:
    SessionStore(_key(), str(tmp_path)).save(111, "secret-session-value")
    raw = (tmp_path / "111.session.enc").read_bytes()
    assert b"secret-session-value" not in raw


def test_wrong_key_cannot_decrypt(tmp_path: Path) -> None:
    SessionStore(_key(), str(tmp_path)).save(111, "session-data")

    other = SessionStore(_key(), str(tmp_path))
    with pytest.raises(InvalidToken):
        other.load(111)


def test_save_overwrites_previous_session(tmp_path: Path) -> None:
    store = SessionStore(_key(), str(tmp_path))
    store.save(111, "first")
    store.save(111, "second")
    assert store.load(111) == "second"


def test_invalid_key_rejected_on_construction(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        SessionStore("not-a-valid-fernet-key", str(tmp_path))


def test_list_senders_returns_saved_ids(tmp_path: Path) -> None:
    store = SessionStore(_key(), str(tmp_path))
    store.save(222, "b")
    store.save(111, "a")
    assert store.list_senders() == [111, 222]


def test_list_senders_skips_foreign_files(tmp_path: Path) -> None:
    store = SessionStore(_key(), str(tmp_path))
    store.save(111, "a")
    # Посторонние файлы в каталоге не должны ломать перечисление.
    (tmp_path / "garbage.session.enc").write_bytes(b"junk")
    (tmp_path / "readme.txt").write_text("not a session")
    assert store.list_senders() == [111]


def test_list_senders_empty_when_dir_missing(tmp_path: Path) -> None:
    store = SessionStore(_key(), str(tmp_path / "absent"))
    assert store.list_senders() == []
