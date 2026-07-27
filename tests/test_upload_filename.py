"""Загрузка креатива обязана слать реальное имя файла с расширением.

VK определяет формат медиа по имени файла, а не по содержимому: тот же PNG,
отправленный под именем `creative` без расширения, отвергается с
`format_not_supported` (боевая проверка 2026-07-27). Ошибка блокировала создание
любой кампании с креативом, поэтому закрепляем тестом.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import respx
from integrations.vk_api import BASE_URL, VkApiAdapter
from pydantic import SecretStr

_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _adapter() -> VkApiAdapter:
    return VkApiAdapter(SecretStr("test-token"))


def _uploaded_filename(route: respx.Route) -> str:
    """Достать имя файла из multipart-тела запроса."""
    body = route.calls.last.request.content.decode("latin-1")
    marker = 'filename="'
    start = body.index(marker) + len(marker)
    return body[start : body.index('"', start)]


@respx.mock
def test_upload_sends_real_filename_with_extension(tmp_path: Path) -> None:
    creative = tmp_path / "icon.png"
    creative.write_bytes(_PNG_BYTES)
    route = respx.post(f"{BASE_URL}/content/static.json").mock(
        return_value=httpx.Response(200, json={"id": 123115052})
    )

    media_id = asyncio.run(_adapter().upload_creative("18096278", str(creative)))

    assert media_id == "123115052"
    assert _uploaded_filename(route) == "icon.png"


@respx.mock
def test_upload_keeps_video_extension(tmp_path: Path) -> None:
    # Видео уходит в СВОЙ эндпоинт: в статике оно отвергается как
    # `format_not_supported` (боевая проверка 2026-07-27).
    creative = tmp_path / "promo.mp4"
    creative.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    route = respx.post(f"{BASE_URL}/content/video.json").mock(
        return_value=httpx.Response(200, json={"id": 42})
    )

    asyncio.run(_adapter().upload_creative("18096278", str(creative)))

    assert _uploaded_filename(route) == "promo.mp4"
