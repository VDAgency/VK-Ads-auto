"""Тесты нормализации хэштегов (`services/hashtags.py`)."""

from __future__ import annotations

import pytest
from services.hashtags import HashtagError, append_hashtags, normalize_hashtags


def test_normalize_splits_by_delimiters_and_dedupes_case_insensitively() -> None:
    assert normalize_hashtags("вкусно, кофе #Кофе  утро") == ["#вкусно", "#кофе", "#утро"]


def test_normalize_empty_input_returns_empty_list() -> None:
    assert normalize_hashtags("") == []
    assert normalize_hashtags("   ") == []


def test_normalize_rejects_invalid_characters() -> None:
    with pytest.raises(HashtagError) as excinfo:
        normalize_hashtags("#a-b")
    assert excinfo.value.code == "invalid_tag"


def test_normalize_rejects_more_than_ten_tags() -> None:
    raw = " ".join(f"tag{i}" for i in range(11))
    with pytest.raises(HashtagError) as excinfo:
        normalize_hashtags(raw)
    assert excinfo.value.code == "too_many"


def test_normalize_rejects_tag_longer_than_fifty_chars() -> None:
    with pytest.raises(HashtagError) as excinfo:
        normalize_hashtags("а" * 51)
    assert excinfo.value.code == "tag_too_long"


def test_normalize_accepts_tag_at_fifty_chars() -> None:
    tag = "а" * 50
    assert normalize_hashtags(tag) == [f"#{tag}"]


def test_append_hashtags_to_existing_text() -> None:
    assert append_hashtags("Текст", ["#a"], 2000) == "Текст\n#a"


def test_append_hashtags_to_missing_text() -> None:
    assert append_hashtags(None, ["#a"], 2000) == "#a"


def test_append_hashtags_without_tags_returns_body_unchanged() -> None:
    assert append_hashtags("Текст", [], 2000) == "Текст"
    assert append_hashtags(None, [], 2000) == ""


def test_append_hashtags_over_limit_raises_text_too_long() -> None:
    with pytest.raises(HashtagError) as excinfo:
        append_hashtags("Текст" * 400, ["#a"], 2000)
    assert excinfo.value.code == "text_too_long"
