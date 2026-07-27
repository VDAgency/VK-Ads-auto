"""Сборка статичного ролика из присланной картинки (решение Вячеслава 2026-07-27).

Зачем. Часть шаблонов VK принимает только видео, а клиент присылает фотографию —
человек, который не умеет монтировать, ролик не сделает. Поэтому конвертируем сами:
одна картинка → короткое видео нужного соотношения сторон.

Как. Модуль внутри системы плюс ffmpeg в образе — БЕЗ отдельного микросервиса
(явное решение Вячеслава: лишний контейнер со своим health-check и сетью не оправдан).
Если ffmpeg в системе нет, честно поднимаем `VideoUnavailable`, а вызывающий откатывается
на картиночный шаблон — молча подсовывать не то мы не будем.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path

from integrations.vk_surfaces import RATIO_VALUES, slot_size

logger = logging.getLogger(__name__)

# Длительность ролика. Пять секунд проходят в любой слот: самый жёсткий предел
# среди подписных шаблонов — 30 секунд.
DEFAULT_SECONDS = 5

# Размер кадра под каждое соотношение сторон. Чётные стороны обязательны: кодек
# H.264 с yuv420p не берёт нечётные размеры.
FRAME_SIZES: dict[str, tuple[int, int]] = {
    "1:1": (1080, 1080),
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
}


class VideoUnavailable(RuntimeError):
    """ffmpeg недоступен или не смог собрать ролик."""


def ffmpeg_path() -> str | None:
    """Путь к ffmpeg или None, если его нет в системе."""
    return shutil.which("ffmpeg")


def frame_size(ratio: str) -> tuple[int, int]:
    """Размер кадра под соотношение сторон; незнакомое — квадрат."""
    return FRAME_SIZES.get(ratio, FRAME_SIZES["1:1"])


def _filter(width: int, height: int) -> str:
    """Вписать картинку в кадр целиком и дополнить белым.

    Кадр не обрезаем: на рекламном макете по краям обычно лежит текст, и `crop`
    съел бы именно его. Пропорции не искажаем по той же причине.
    """
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:white,"
        f"format=yuv420p"
    )


def _build(source: str, destination: Path, ratio: str, seconds: int) -> Path:
    binary = ffmpeg_path()
    if binary is None:
        raise VideoUnavailable("ffmpeg не установлен")

    width, height = frame_size(ratio)
    destination.parent.mkdir(parents=True, exist_ok=True)
    command = [
        binary,
        "-y",
        "-loop",
        "1",
        "-i",
        source,
        "-t",
        str(seconds),
        # Поток без звука VK принимает; отдельная тишина не нужна.
        "-vf",
        _filter(width, height),
        "-r",
        "25",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-pix_fmt",
        "yuv420p",
        # Ключевой кадр в начале — иначе часть плееров не покажет превью.
        "-g",
        "25",
        "-movflags",
        "+faststart",
        str(destination),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=180)  # noqa: S603
    if result.returncode != 0 or not destination.exists():
        tail = (result.stderr or "").strip().splitlines()[-3:]
        raise VideoUnavailable(f"ffmpeg вернул {result.returncode}: {' / '.join(tail)}")
    return destination


async def image_to_video(
    creative_ref: str,
    ratio: str,
    out_dir: Path,
    *,
    seconds: int = DEFAULT_SECONDS,
) -> Path:
    """Собрать ролик из картинки и вернуть путь к нему.

    Соотношение сторон берётся у шаблона, под который собирается объявление, а не у
    исходной картинки: слот диктует кадр, картинка в него вписывается.
    """
    if ratio not in RATIO_VALUES:
        raise VideoUnavailable(f"Неизвестное соотношение сторон: {ratio!r}")
    destination = out_dir / f"{Path(creative_ref).stem}_{ratio.replace(':', 'x')}.mp4"
    return await asyncio.to_thread(_build, creative_ref, destination, ratio, seconds)


def icon_from_image(creative_ref: str, out_dir: Path) -> Path:
    """Иконка 256×256 из той же картинки — она обязательна в каждом шаблоне.

    Вынесено сюда, чтобы видео-ветка не тянула за собой Pillow-хелперы картинок.
    """
    from integrations.vk_creative_formats import fit_to_slot

    size = slot_size("icon_256x256")
    assert size is not None  # иконка всегда есть в справочнике
    return fit_to_slot(creative_ref, size, out_dir)
