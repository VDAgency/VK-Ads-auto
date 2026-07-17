"""CredentialStore/StateStore — Fernet round-trip, 0600, ключи (spec §4, K-PR1)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from cryptography.fernet import Fernet, InvalidToken
from kotbot.store import CredentialStore, NotConfiguredError, StateStore


def _key() -> str:
    return Fernet.generate_key().decode("ascii")


# --- CredentialStore -----------------------------------------------------------


def test_credentials_round_trip(tmp_path: Path) -> None:
    store = CredentialStore(_key(), str(tmp_path / "sub"))

    store.save("email", "ops@example.com", "p@ssw0rd")

    assert store.load("email") == ("ops@example.com", "p@ssw0rd")
    assert store.has("email") is True


def test_credentials_strategies_are_merged_not_overwritten(tmp_path: Path) -> None:
    store = CredentialStore(_key(), str(tmp_path))
    store.save("email", "ops@example.com", "email-pass")
    store.save("vk", "+79990001122", "vk-pass")

    # Обе стратегии живут в одном файле, сохранение одной не затирает другую.
    assert store.load("email") == ("ops@example.com", "email-pass")
    assert store.load("vk") == ("+79990001122", "vk-pass")


def test_credentials_save_overwrites_same_strategy(tmp_path: Path) -> None:
    store = CredentialStore(_key(), str(tmp_path))
    store.save("email", "first@example.com", "one")
    store.save("email", "second@example.com", "two")
    assert store.load("email") == ("second@example.com", "two")


def test_credentials_missing_strategy_returns_none(tmp_path: Path) -> None:
    store = CredentialStore(_key(), str(tmp_path))
    assert store.load("email") is None
    assert store.has("email") is False


def test_credentials_file_is_encrypted_not_plaintext(tmp_path: Path) -> None:
    CredentialStore(_key(), str(tmp_path)).save("email", "ops@example.com", "top-secret-pass")
    raw = (tmp_path / "credentials.enc").read_bytes()
    assert b"top-secret-pass" not in raw
    assert b"ops@example.com" not in raw


def test_credentials_wrong_key_cannot_decrypt(tmp_path: Path) -> None:
    CredentialStore(_key(), str(tmp_path)).save("email", "l", "p")

    other = CredentialStore(_key(), str(tmp_path))
    with pytest.raises(InvalidToken):
        other.load("email")
    # has() для /health не бросает — честно говорит «кредов нет».
    assert other.has("email") is False


# --- StateStore ------------------------------------------------------------------


def test_state_round_trip_per_strategy(tmp_path: Path) -> None:
    store = StateStore(_key(), str(tmp_path))
    store.save_raw("email", b'{"cookies": ["email"]}')
    store.save_raw("vk", b'{"cookies": ["vk"]}')

    assert store.load_raw("email") == b'{"cookies": ["email"]}'
    assert store.load_raw("vk") == b'{"cookies": ["vk"]}'
    assert (tmp_path / "state.email.json.enc").exists()
    assert (tmp_path / "state.vk.json.enc").exists()


def test_state_missing_returns_none(tmp_path: Path) -> None:
    store = StateStore(_key(), str(tmp_path))
    assert store.load_raw("email") is None
    assert store.has("email") is False


def test_state_file_is_encrypted(tmp_path: Path) -> None:
    StateStore(_key(), str(tmp_path)).save_raw("email", b"sso-cookie-payload")
    raw = (tmp_path / "state.email.json.enc").read_bytes()
    assert b"sso-cookie-payload" not in raw


def test_state_wrong_key_cannot_decrypt(tmp_path: Path) -> None:
    StateStore(_key(), str(tmp_path)).save_raw("email", b"data")
    with pytest.raises(InvalidToken):
        StateStore(_key(), str(tmp_path)).load_raw("email")


# --- Права файлов (только POSIX: chmod на Windows не даёт 0600) -----------------


@pytest.mark.skipif(os.name != "posix", reason="POSIX-права проверяемы только на Unix")
def test_saved_files_are_0600(tmp_path: Path) -> None:
    key = _key()
    CredentialStore(key, str(tmp_path)).save("email", "l", "p")
    StateStore(key, str(tmp_path)).save_raw("email", b"data")

    for name in ("credentials.enc", "state.email.json.enc"):
        mode = stat.S_IMODE((tmp_path / name).stat().st_mode)
        assert mode == 0o600


# --- Пустой / некорректный ключ ---------------------------------------------------


def test_empty_key_marks_not_configured(tmp_path: Path) -> None:
    creds = CredentialStore("", str(tmp_path))
    states = StateStore("", str(tmp_path))
    assert creds.configured is False
    assert states.configured is False
    # has() дёшев и не бросает — /health должен работать без ключа.
    assert creds.has("email") is False
    assert states.has("email") is False


def test_empty_key_operations_raise_not_configured(tmp_path: Path) -> None:
    creds = CredentialStore("", str(tmp_path))
    states = StateStore("", str(tmp_path))

    with pytest.raises(NotConfiguredError):
        creds.save("email", "l", "p")
    with pytest.raises(NotConfiguredError):
        creds.load("email")
    with pytest.raises(NotConfiguredError):
        states.save_raw("email", b"data")
    with pytest.raises(NotConfiguredError):
        states.load_raw("email")


def test_invalid_key_rejected_on_construction(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        CredentialStore("not-a-valid-fernet-key", str(tmp_path))
    with pytest.raises(ValueError):
        StateStore("not-a-valid-fernet-key", str(tmp_path))
