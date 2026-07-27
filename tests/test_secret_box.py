"""Шифрование секретов для колонок БД (spec 2026-07-27 §6)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from services.secret_box import InvalidToken, NotConfiguredError, SecretBox, token_tail

TOKEN = "fake-access-token-for-tests-0000000000000000"


def _key() -> str:
    return Fernet.generate_key().decode("ascii")


def test_round_trip_returns_original_secret() -> None:
    box = SecretBox(_key())
    assert box.decrypt(box.encrypt(TOKEN)) == TOKEN


def test_ciphertext_does_not_contain_plaintext() -> None:
    """Главное свойство: в БД не должно оказаться самого токена."""
    box = SecretBox(_key())
    assert TOKEN not in box.encrypt(TOKEN)


def test_encrypt_is_not_deterministic() -> None:
    """Fernet солит каждый шифротекст — одинаковые токены не сравнить по значению."""
    box = SecretBox(_key())
    assert box.encrypt(TOKEN) != box.encrypt(TOKEN)


def test_unicode_secret_survives_round_trip() -> None:
    box = SecretBox(_key())
    assert box.decrypt(box.encrypt("Студия «Пример»")) == "Студия «Пример»"


def test_wrong_key_cannot_decrypt() -> None:
    encrypted = SecretBox(_key()).encrypt(TOKEN)
    with pytest.raises(InvalidToken):
        SecretBox(_key()).decrypt(encrypted)


def test_empty_key_means_not_configured() -> None:
    box = SecretBox("")
    assert box.configured is False
    with pytest.raises(NotConfiguredError):
        box.encrypt(TOKEN)
    with pytest.raises(NotConfiguredError):
        box.decrypt("whatever")


def test_non_empty_key_is_configured() -> None:
    assert SecretBox(_key()).configured is True


def test_invalid_key_rejected_on_construction() -> None:
    """Кривой ключ — ошибка конфигурации, а не тихая работа без шифрования."""
    with pytest.raises(ValueError):
        SecretBox("not-a-fernet-key")


def test_token_tail_shows_last_four_chars() -> None:
    assert token_tail("abcdefgh") == "efgh"


def test_token_tail_hides_short_secrets_entirely() -> None:
    """Короткая строка не должна раскрыться целиком под видом маски."""
    assert token_tail("abcd") == ""
    assert token_tail("ab") == ""
    assert token_tail("") == ""
