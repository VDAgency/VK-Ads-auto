"""Сборка ролика из картинки: кадр, команда ffmpeg и честный отказ.

Часть шаблонов VK принимает только видео, а клиент присылает фотографию. Ролик мы
собираем сами; здесь закрепляем, что кадр соответствует слоту, картинка вписывается
целиком (а не обрезается), и что отсутствие ffmpeg не выливается в тихую подмену.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest
from integrations import vk_video
from integrations.vk_surfaces import RATIO_VALUES
from integrations.vk_video import (
    FRAME_SIZES,
    VideoUnavailable,
    frame_size,
    image_to_video,
)


def test_every_supported_ratio_has_a_frame_size() -> None:
    # Соотношения берутся у шаблона объявления — кадр обязан найтись для любого.
    assert set(FRAME_SIZES) == set(RATIO_VALUES)


def test_frame_sizes_are_even() -> None:
    # H.264 с yuv420p не берёт нечётные стороны — ролик просто не соберётся.
    for ratio, (width, height) in FRAME_SIZES.items():
        assert width % 2 == 0 and height % 2 == 0, ratio


def test_unknown_ratio_falls_back_to_square() -> None:
    assert frame_size("нет такого") == FRAME_SIZES["1:1"]


def test_missing_ffmpeg_is_reported_not_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vk_video, "ffmpeg_path", lambda: None)
    with pytest.raises(VideoUnavailable, match="ffmpeg"):
        asyncio.run(image_to_video("pic.png", "1:1", Path("/tmp/out")))


def test_unknown_ratio_is_rejected_before_touching_ffmpeg() -> None:
    with pytest.raises(VideoUnavailable, match="оотношение"):
        asyncio.run(image_to_video("pic.png", "3:2", Path("/tmp/out")))


def test_command_fits_the_image_into_the_frame_without_cropping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        recorded.append(command)
        Path(command[-1]).write_bytes(b"fake mp4")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(vk_video, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr("integrations.vk_video.subprocess.run", fake_run)

    out = asyncio.run(image_to_video("pic.png", "9:16", tmp_path))

    assert out.suffix == ".mp4"
    command = recorded[0]
    filters = command[command.index("-vf") + 1]
    # Вписываем и добавляем поля, а не обрезаем: по краям макета обычно текст.
    assert "force_original_aspect_ratio=decrease" in filters
    assert "pad=1080:1920" in filters
    assert "crop" not in filters
    assert "yuv420p" in command
    assert command[command.index("-t") + 1] == str(vk_video.DEFAULT_SECONDS)


def test_ffmpeg_failure_is_reported_with_its_own_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "Unknown encoder 'libx264'")

    monkeypatch.setattr(vk_video, "ffmpeg_path", lambda: "ffmpeg")
    monkeypatch.setattr("integrations.vk_video.subprocess.run", failing_run)

    with pytest.raises(VideoUnavailable, match="libx264"):
        asyncio.run(image_to_video("pic.png", "1:1", tmp_path))


@pytest.mark.skipif(vk_video.ffmpeg_path() is None, reason="ffmpeg не установлен локально")
def test_real_ffmpeg_produces_a_playable_file(tmp_path: Path) -> None:
    from PIL import Image

    source = tmp_path / "pic.png"
    Image.new("RGB", (1024, 1024), (20, 40, 60)).save(source)

    out = asyncio.run(image_to_video(str(source), "9:16", tmp_path / "vid"))
    assert out.exists() and out.stat().st_size > 1000


def test_video_ad_from_a_static_image_uses_the_video_slot(tmp_path: Path) -> None:
    """Просьба «сделай видео» переводит объявление на видео-шаблон.

    Без этого ветка сборки ролика недостижима: под картинку всегда находится
    картиночный шаблон, и ffmpeg никогда бы не позвали.
    """
    from integrations.vk_creative_formats import pattern_for_creative
    from integrations.vk_surfaces import VK_COMMUNITY
    from PIL import Image

    source = tmp_path / "tall.png"
    Image.new("RGB", (1080, 1920), (1, 2, 3)).save(source)

    plain = pattern_for_creative(VK_COMMUNITY, str(source))
    asked = pattern_for_creative(VK_COMMUNITY, str(source), prefer_video=True)

    assert not plain.is_video and plain.media_slot.startswith("image_")
    assert asked.is_video and asked.ratio == plain.ratio


def test_video_request_is_ignored_where_the_surface_has_no_video(tmp_path: Path) -> None:
    # У Дзена видео-шаблонов нет: отказывать клиенту из-за формата нечестно,
    # поэтому просьба тихо игнорируется в пользу картинки.
    from integrations.vk_creative_formats import pattern_for_creative
    from integrations.vk_surfaces import DZEN_CHANNEL
    from PIL import Image

    source = tmp_path / "tall.png"
    Image.new("RGB", (1080, 1920), (1, 2, 3)).save(source)

    assert not pattern_for_creative(DZEN_CHANNEL, str(source), prefer_video=True).is_video


def test_video_and_images_go_to_different_upload_endpoints(tmp_path: Path) -> None:
    """Ролик в эндпоинт статики VK не принимает — проверяем выбор адреса загрузки."""
    import asyncio as _asyncio

    import httpx
    import respx
    from integrations.vk_api import (
        BASE_URL,
        STATIC_UPLOAD_PATH,
        VIDEO_UPLOAD_PATH,
        VkApiAdapter,
    )
    from pydantic import SecretStr

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"fake mp4")
    picture = tmp_path / "pic.png"
    picture.write_bytes(b"fake png")

    with respx.mock:
        static = respx.post(f"{BASE_URL}{STATIC_UPLOAD_PATH}").mock(
            return_value=httpx.Response(200, json={"id": 1})
        )
        video = respx.post(f"{BASE_URL}{VIDEO_UPLOAD_PATH}").mock(
            return_value=httpx.Response(200, json={"id": 2})
        )
        adapter = VkApiAdapter(SecretStr("t"))
        assert _asyncio.run(adapter.upload_creative("cab", str(picture))) == "1"
        assert _asyncio.run(adapter.upload_creative("cab", str(clip))) == "2"
        assert static.called and video.called
