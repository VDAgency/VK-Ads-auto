"""Тесты сохранения креатива на диск (`services/creative_store`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from config.settings import Settings
from services.creative_store import save_creative


def test_save_creative_writes_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.creative_store.get_settings",
        lambda: Settings(_env_file=None, creatives_dir=str(tmp_path)),
    )
    path = save_creative(7, "photo", b"\xff\xd8\xff\x00")

    saved = Path(path)
    assert saved.exists()
    assert saved.read_bytes() == b"\xff\xd8\xff\x00"
    assert saved.suffix == ".jpg"
    # Разложено по подкаталогу брифа внутри CREATIVES_DIR.
    assert saved.parent == tmp_path / "7"


def test_save_creative_video_extension(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "services.creative_store.get_settings",
        lambda: Settings(_env_file=None, creatives_dir=str(tmp_path)),
    )
    path = save_creative(7, "video", b"\x00\x00\x00")
    assert Path(path).suffix == ".mp4"
