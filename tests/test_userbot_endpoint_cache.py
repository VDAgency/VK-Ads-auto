"""Кэш удачной точки подключения: запись, чтение и устойчивость к мусору.

Кэш — вспомогательная оптимизация, поэтому главное требование: он никогда не должен
мешать старту сервиса. Битый файл — это предупреждение в лог, а не исключение.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from userbot.endpoint_cache import EndpointCache
from userbot.endpoints import Endpoint, Transport

_ENDPOINT = Endpoint(dc_id=4, ip="149.154.167.91", port=5222, transport=Transport.OBFUSCATED)


def test_remember_and_read_back(tmp_path: Path) -> None:
    cache = EndpointCache(str(tmp_path))
    cache.remember(111, _ENDPOINT)
    assert EndpointCache(str(tmp_path)).get(111) == _ENDPOINT


def test_unknown_sender_returns_none(tmp_path: Path) -> None:
    assert EndpointCache(str(tmp_path)).get(999) is None


@pytest.mark.skipif(os.name != "posix", reason="POSIX-права проверяемы только на Unix")
def test_file_is_owner_only(tmp_path: Path) -> None:
    cache = EndpointCache(str(tmp_path))
    cache.remember(111, _ENDPOINT)
    mode = (tmp_path / "endpoints.json").stat().st_mode
    assert not mode & stat.S_IRGRP and not mode & stat.S_IROTH


def test_broken_json_does_not_break_startup(tmp_path: Path) -> None:
    (tmp_path / "endpoints.json").write_text("{это не json", encoding="utf-8")
    cache = EndpointCache(str(tmp_path))
    assert cache.get(111) is None
    cache.remember(111, _ENDPOINT)
    assert cache.get(111) == _ENDPOINT


def test_unexpected_shape_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "endpoints.json").write_text('["список вместо словаря"]', encoding="utf-8")
    assert EndpointCache(str(tmp_path)).get(111) is None


def test_broken_entry_is_skipped_but_good_one_kept(tmp_path: Path) -> None:
    payload = {
        "111": {"dc_id": "не число"},
        "222": {"dc_id": 4, "ip": "149.154.167.91", "port": 5222, "transport": "obfuscated"},
    }
    (tmp_path / "endpoints.json").write_text(json.dumps(payload), encoding="utf-8")
    cache = EndpointCache(str(tmp_path))
    assert cache.get(111) is None
    assert cache.get(222) == _ENDPOINT


def test_unknown_transport_falls_back_to_full(tmp_path: Path) -> None:
    payload = {"111": {"dc_id": 1, "ip": "1.2.3.4", "port": 443, "transport": "неизвестный"}}
    (tmp_path / "endpoints.json").write_text(json.dumps(payload), encoding="utf-8")
    endpoint = EndpointCache(str(tmp_path)).get(111)
    assert endpoint is not None
    assert endpoint.transport is Transport.FULL


def test_remember_overwrites_previous_endpoint(tmp_path: Path) -> None:
    cache = EndpointCache(str(tmp_path))
    cache.remember(111, Endpoint(dc_id=4, ip="149.154.167.91", port=443))
    cache.remember(111, _ENDPOINT)
    assert EndpointCache(str(tmp_path)).get(111) == _ENDPOINT


def test_no_temp_file_left_behind(tmp_path: Path) -> None:
    cache = EndpointCache(str(tmp_path))
    cache.remember(111, _ENDPOINT)
    assert not list(tmp_path.glob("*.tmp"))
