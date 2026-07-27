"""Работа с медиафайлом креатива: тип, размеры, приведение к слоту VK.

Какие форматы вообще принимает та или иная площадка — знает `integrations/vk_surfaces.py`
(там же и происхождение данных). Здесь только механика над самим файлом: определить,
картинка это или видео, прочитать размеры и подогнать кадр под размер слота.
"""

from __future__ import annotations

import struct
from pathlib import Path

from integrations.vk_surfaces import (
    ICON_SIZE,
    ICON_SLOT,
    Pattern,
    Surface,
    pick_pattern,
    ratio_of,
    slot_size,
)

__all__ = [
    "ICON_SIZE",
    "ICON_SLOT",
    "VIDEO_SUFFIXES",
    "fit_to_slot",
    "image_size",
    "is_video",
    "pattern_for_creative",
    "slot_size",
]

VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm"})


def is_video(creative_ref: str) -> bool:
    """Видео это или картинка — по расширению файла."""
    return Path(creative_ref).suffix.lower() in VIDEO_SUFFIXES


def image_size(creative_ref: str) -> tuple[int, int]:
    """Размеры картинки (PNG/JPEG) без сторонних библиотек.

    Читаем заголовок сами: подбор шаблона нужен ещё до всякой обработки картинки, а
    открывать файл целиком ради двух чисел незачем. PNG — размеры лежат в IHDR по
    фиксированному смещению; JPEG — идём по сегментам до маркера SOF.
    """
    data = Path(creative_ref).read_bytes()

    if data[:8] == b"\x89PNG\r\n\x1a\n":
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)

    if data[:2] == b"\xff\xd8":  # JPEG
        offset = 2
        while offset + 9 < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            # SOF0..SOF15, кроме DHT(C4), JPG(C8) и DAC(CC) — в них размеров нет.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[offset + 5 : offset + 9])
                return int(width), int(height)
            segment = struct.unpack(">H", data[offset + 2 : offset + 4])[0]
            offset += 2 + segment
        raise ValueError(f"Не удалось прочитать размеры JPEG: {creative_ref}")

    raise ValueError(f"Неподдерживаемый формат картинки: {creative_ref}")


def pattern_for_creative(
    surface: Surface, creative_ref: str, *, prefer_video: bool = False
) -> Pattern:
    """Подобрать шаблон объявления под присланный файл и выбранную площадку.

    У видео размеры не читаем — соотношение сторон контейнера без разбора кодека не
    получить, а площадки принимают квадрат почти везде. Для картинки соотношение
    считаем честно по заголовку файла.

    `prefer_video` — просьба сделать видео-объявление из статичной картинки: ролик
    соберётся сам (`integrations/vk_video.py`). Если у площадки видео-шаблонов нет
    (Дзен, Одноклассники в вертикали), просьба игнорируется и берётся картиночный
    шаблон — отказывать клиенту из-за формата нечестно.
    """
    if is_video(creative_ref):
        return pick_pattern(surface, ratio="1:1", is_video=True)

    width, height = image_size(creative_ref)
    ratio = ratio_of(width, height)
    if prefer_video and surface.patterns_for(is_video=True):
        return pick_pattern(surface, ratio=ratio, is_video=True)
    return pick_pattern(surface, ratio=ratio, is_video=False)


def fit_to_slot(creative_ref: str, target: tuple[int, int], out_dir: Path) -> Path:
    """Привести картинку к размеру слота и вернуть путь к готовому файлу.

    Загрузку медиа VK принимает в любом размере, но при сборке объявления сверяет его
    со слотом: 1024×1024 в `icon_256x256` → `bad_width: Maximum width is 256`
    (боевая проверка 2026-07-27). Поэтому уменьшаем сами, а не просим у клиента.

    Кадр вписывается целиком и центрируется на поле нужного размера: так не теряются
    края макета и не искажаются пропорции. Прозрачность сводится на белый — VK
    показывает объявления на светлой подложке.
    """
    from PIL import Image  # локальный импорт: Pillow нужен только здесь

    width, height = target
    out_dir.mkdir(parents=True, exist_ok=True)
    destination = out_dir / f"{Path(creative_ref).stem}_{width}x{height}.png"

    with Image.open(creative_ref) as source:
        image = source.convert("RGBA")
        image.thumbnail((width, height), Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", (width, height), (255, 255, 255))
        canvas.paste(image, ((width - image.width) // 2, (height - image.height) // 2), image)
        canvas.save(destination, format="PNG")

    return destination
