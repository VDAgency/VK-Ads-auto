"""Валидация креатива против требований VK (Фаза 5).

Лимиты VK зависят от формата/пакета и меняются несколько раз в год (CLAUDE.md §6) —
здесь базовые значения; перед боем сверять с актуальными требованиями VK/пакета
(docs/VK_API_REFERENCE.md: слоты content/textblocks читаются из пакета). Возвращаем
список понятных оператору проблем; пустой список = креатив валиден.
"""

from __future__ import annotations

from dataclasses import dataclass

ALLOWED_IMAGE_FORMATS = {"jpg", "jpeg", "png"}
MIN_IMAGE_SIDE = 600
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 МБ
MAX_VIDEO_BYTES = 500 * 1024 * 1024  # 500 МБ
MAX_TITLE_LEN = 40
MAX_TEXT_LEN = 220


@dataclass(frozen=True)
class ImageCreative:
    """Параметры изображения для проверки."""

    fmt: str
    width: int
    height: int
    size_bytes: int


def validate_image(image: ImageCreative) -> list[str]:
    """Проверить изображение; вернуть список проблем (пусто = ок)."""
    issues: list[str] = []
    if image.fmt.lower() not in ALLOWED_IMAGE_FORMATS:
        issues.append(f"Формат «{image.fmt}» не поддерживается — нужен JPG или PNG.")
    if image.width < MIN_IMAGE_SIDE or image.height < MIN_IMAGE_SIDE:
        issues.append(f"Минимальный размер изображения {MIN_IMAGE_SIDE}×{MIN_IMAGE_SIDE} px.")
    if image.size_bytes > MAX_IMAGE_BYTES:
        issues.append("Файл изображения больше 10 МБ.")
    return issues


def validate_video_size(size_bytes: int) -> list[str]:
    """Проверить размер видео."""
    if size_bytes > MAX_VIDEO_BYTES:
        return ["Видео больше 500 МБ."]
    return []


def validate_text(title: str, body: str) -> list[str]:
    """Проверить длины заголовка и текста."""
    issues: list[str] = []
    if len(title) > MAX_TITLE_LEN:
        issues.append(f"Заголовок длиннее {MAX_TITLE_LEN} символов.")
    if len(body) > MAX_TEXT_LEN:
        issues.append(f"Текст длиннее {MAX_TEXT_LEN} символов.")
    return issues


def is_valid(issues: list[str]) -> bool:
    """True, если проблем нет."""
    return not issues
