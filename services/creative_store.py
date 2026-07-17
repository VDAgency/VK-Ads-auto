"""Сохранение загруженного креатива на диск (persistent volume РФ-сервера).

Медиа приходит из бота (оператор загрузил в чат) и складывается в `CREATIVES_DIR`;
в БД (`Creative`) хранится только путь и метаданные. ПДн/бизнес-материалы клиента
не покидают РФ-сервер (152-ФЗ).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from config.settings import get_settings

# Расширение файла по типу медиа (Telegram фото — JPEG, видео — MP4).
_EXT = {"photo": "jpg", "video": "mp4"}


def save_creative(brief_id: int, media_type: str, data: bytes) -> str:
    """Сохранить байты медиа под бриф; вернуть путь к файлу (ref для запуска кампании)."""
    ext = _EXT.get(media_type, "bin")
    target_dir = Path(get_settings().creatives_dir) / str(brief_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{uuid.uuid4().hex}.{ext}"
    path.write_bytes(data)
    return str(path)
