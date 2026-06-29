from services.edit_parser import parse_edits


def test_single_edit() -> None:
    result = parse_edits("1. подписчики")
    assert result.edits == {1: "подписчики"}
    assert result.invalid_lines == []


def test_multiple_edits_with_blank_line() -> None:
    result = parse_edits("1. подписчики\n\n5. 30000")
    assert result.edits == {1: "подписчики", 5: "30000"}
    assert result.is_empty() is False


def test_spacing_variants() -> None:
    assert parse_edits("3.значение").edits == {3: "значение"}
    assert parse_edits("  7 .  два слова  ").edits == {7: "два слова"}


def test_value_may_contain_dot() -> None:
    # Разделитель — первая точка после номера; остальное идёт в значение.
    assert parse_edits("5. 30.000").edits == {5: "30.000"}


def test_duplicate_number_last_wins() -> None:
    assert parse_edits("2. первое\n2. второе").edits == {2: "второе"}


def test_invalid_lines_collected() -> None:
    result = parse_edits("1. ок\nпросто текст\n12.")
    assert result.edits == {1: "ок"}
    assert result.invalid_lines == ["просто текст", "12."]


def test_empty_message_is_empty() -> None:
    result = parse_edits("\n   \n")
    assert result.is_empty() is True
    assert result.edits == {}
