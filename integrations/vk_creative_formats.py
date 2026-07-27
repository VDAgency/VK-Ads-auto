"""Справочник допустимых форматов креатива VK Ads для цели «подписчики».

Источник — живой `GET /banner_patterns.json` (снято 2026-07-27). VK принимает объявление,
только если его содержимое совпадает с одним из шаблонов (`patterns`), разрешённых пакетом;
иначе приходит `bad_value: At least one pattern must be in package's settings`.

Устройство любого шаблона одинаковое: **иконка 256×256 обязательна всегда**, плюс ровно один
основной медиа-слот — картинка или видео. Различается кнопка: `cta_profile_vk` у личной
страницы, `cta_community_vk` у сообщества.

⚠️ Ключевое ограничение: **у личной страницы нет вертикальных форматов** — только квадрат и
альбом. Вертикаль (9:16 и 4:5) доступна лишь сообществам. Прислали вертикальную картинку под
личную страницу — её придётся кадрировать, а не просто загрузить.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from services.mapping import OBJECT_KIND_PERSONAL

# Иконка нужна каждому шаблону без исключений.
ICON_SLOT = "icon_256x256"
ICON_SIZE = (256, 256)

# Кнопка призыва к действию зависит от типа объекта рекламы.
CTA_SLOT_PERSONAL = "cta_profile_vk"
CTA_SLOT_COMMUNITY = "cta_community_vk"


@dataclass(frozen=True)
class CreativeFormat:
    """Один основной медиа-слот: что это, какого размера и в какие шаблоны входит."""

    slot: str
    is_video: bool
    width: int | None  # None — задано только соотношение сторон
    height: int | None
    ratio: str
    max_seconds: int | None
    patterns: tuple[int, ...]

    @property
    def is_portrait(self) -> bool:
        return self.ratio in {"9:16", "4:5"}


# --- Личная страница (пакет 3268) --------------------------------------------------
PERSONAL_FORMATS: tuple[CreativeFormat, ...] = (
    CreativeFormat("image_600x600", False, 600, 600, "1:1", None, (535,)),
    CreativeFormat("image_1080x607", False, 1080, 607, "16:9", None, (519,)),
    CreativeFormat("video_square_1_1_30s", True, None, None, "1:1", 30, (151,)),
    CreativeFormat("video_square_300s", True, None, None, "1:1", 300, (534,)),
    CreativeFormat("video_landscape_16_9_30s", True, None, None, "16:9", 30, (144,)),
    CreativeFormat("video_landscape_300s", True, None, None, "16:9", 300, (520,)),
)

# --- Сообщество (пакет 3122) -------------------------------------------------------
COMMUNITY_FORMATS: tuple[CreativeFormat, ...] = (
    CreativeFormat("image_600x600", False, 600, 600, "1:1", None, (529,)),
    CreativeFormat("image_1080x607", False, 1080, 607, "16:9", None, (400, 422, 426)),
    CreativeFormat("image_607x1080", False, 607, 1080, "9:16", None, (525,)),
    CreativeFormat("image_4_5", False, None, None, "4:5", None, (339,)),
    CreativeFormat("video_square_1_1_30s", True, None, None, "1:1", 30, (153,)),
    CreativeFormat("video_square_300s", True, None, None, "1:1", 300, (530,)),
    CreativeFormat("video_landscape_16_9_30s", True, None, None, "16:9", 30, (152,)),
    CreativeFormat("video_landscape_300s", True, None, None, "16:9", 300, (401, 427)),
    CreativeFormat("video_portrait_9_16_30s", True, None, None, "9:16", 30, (145,)),
    CreativeFormat("video_portrait_9_16_180s", True, None, None, "9:16", 180, (527,)),
    CreativeFormat("video_portrait_4_5_30s", True, None, None, "4:5", 30, (150,)),
    CreativeFormat("video_portrait_4_5_180s", True, None, None, "4:5", 180, (338,)),
)


VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm"})


def is_video(creative_ref: str) -> bool:
    """Видео это или картинка — по расширению файла."""
    return Path(creative_ref).suffix.lower() in VIDEO_SUFFIXES


def image_size(creative_ref: str) -> tuple[int, int]:
    """Размеры картинки (PNG/JPEG) без сторонних библиотек.

    Читаем заголовок сами: подбор слота нужен ещё до всякой обработки картинки, а
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


def slot_size(slot: str) -> tuple[int, int] | None:
    """Точный размер слота, если он задан именем (`image_600x600` → 600×600)."""
    if slot == ICON_SLOT:
        return ICON_SIZE
    for formats in (PERSONAL_FORMATS, COMMUNITY_FORMATS):
        for fmt in formats:
            if fmt.slot == slot and fmt.width and fmt.height:
                return fmt.width, fmt.height
    return None


def formats_for(object_kind: str) -> tuple[CreativeFormat, ...]:
    """Допустимые форматы для типа объекта рекламы."""
    return PERSONAL_FORMATS if object_kind == OBJECT_KIND_PERSONAL else COMMUNITY_FORMATS


def cta_slot(object_kind: str) -> str:
    """Слот кнопки: у личной страницы и сообщества он разный."""
    return CTA_SLOT_PERSONAL if object_kind == OBJECT_KIND_PERSONAL else CTA_SLOT_COMMUNITY


def _ratio_of(width: int, height: int) -> str:
    """Ближайшее из поддерживаемых соотношений сторон."""
    value = width / height
    candidates = {"1:1": 1.0, "16:9": 16 / 9, "9:16": 9 / 16, "4:5": 4 / 5}
    return min(candidates, key=lambda name: abs(candidates[name] - value))


def pick_format(object_kind: str, *, width: int, height: int, is_video: bool) -> CreativeFormat:
    """Подобрать слот под присланный креатив по типу и соотношению сторон.

    Подбираем по соотношению сторон, а не по точным размерам: под нужный размер слота
    картинку всё равно приводит `fit_to_slot` — VK сверяет его при сборке объявления
    (`bad_width`), хотя саму загрузку принимает в любом размере.
    """
    allowed = [fmt for fmt in formats_for(object_kind) if fmt.is_video == is_video]
    if not allowed:
        kind = "видео" if is_video else "картинку"
        raise ValueError(f"VK не принимает {kind} для типа объекта {object_kind!r}")

    ratio = _ratio_of(width, height)
    for fmt in allowed:
        if fmt.ratio == ratio:
            return fmt
    # Вертикали у личной страницы нет — честно говорим, что нужен другой кадр.
    raise ValueError(
        f"Соотношение {ratio} недоступно для {object_kind!r}; "
        f"поддерживаются: {', '.join(sorted({f.ratio for f in allowed}))}"
    )
