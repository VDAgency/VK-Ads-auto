from services.creative_validate import (
    ImageCreative,
    is_valid,
    validate_image,
    validate_text,
    validate_video_size,
)


def test_valid_image_has_no_issues() -> None:
    image = ImageCreative(fmt="png", width=1080, height=1080, size_bytes=500_000)
    assert validate_image(image) == []
    assert is_valid(validate_image(image)) is True


def test_image_too_small() -> None:
    issues = validate_image(ImageCreative(fmt="jpg", width=400, height=400, size_bytes=1000))
    assert any("Минимальный размер" in issue for issue in issues)


def test_image_wrong_format() -> None:
    issues = validate_image(ImageCreative(fmt="gif", width=800, height=800, size_bytes=1000))
    assert any("не поддерживается" in issue for issue in issues)


def test_image_too_big() -> None:
    issues = validate_image(
        ImageCreative(fmt="png", width=800, height=800, size_bytes=11 * 1024 * 1024)
    )
    assert any("больше 10 МБ" in issue for issue in issues)


def test_video_size() -> None:
    assert validate_video_size(100) == []
    assert validate_video_size(600 * 1024 * 1024) != []


def test_text_limits() -> None:
    assert validate_text("ok", "ok") == []
    long = validate_text("a" * 50, "b" * 300)
    assert len(long) == 2
