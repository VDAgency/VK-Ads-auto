"""Разбор строки прокси в аргумент Telethon.

Прокси пока не подключён (переменная пуста), но код и конфигурация готовы заранее:
если Telegram закроет и порт 5222, единственным путём останется туннель.
"""

from __future__ import annotations

import pytest
from userbot.endpoints import Transport
from userbot.proxy import parse_proxy


def test_empty_means_direct_connection() -> None:
    assert parse_proxy("") is None
    assert parse_proxy("   ") is None


def test_socks5_without_credentials() -> None:
    config = parse_proxy("socks5://10.0.0.1:1080")
    assert config is not None
    assert config.value == {
        "proxy_type": "socks5",
        "addr": "10.0.0.1",
        "port": 1080,
        "rdns": True,
    }
    assert config.transport is None


def test_socks5_with_credentials() -> None:
    config = parse_proxy("socks5://user:secret@10.0.0.1:1080")
    assert config is not None
    assert isinstance(config.value, dict)
    assert config.value["username"] == "user"
    assert config.value["password"] == "secret"


def test_rdns_is_enabled() -> None:
    """Без rdns DNS-запросы уходят мимо туннеля и выдают клиента."""
    config = parse_proxy("socks5://10.0.0.1:1080")
    assert config is not None
    assert isinstance(config.value, dict)
    assert config.value["rdns"] is True


def test_mtproxy_forces_its_own_transport() -> None:
    config = parse_proxy("mtproxy://proxy.example.com:2002?secret=deadbeef")
    assert config is not None
    assert config.value == ("proxy.example.com", 2002, "deadbeef")
    assert config.transport is Transport.MTPROXY


@pytest.mark.parametrize(
    "raw",
    [
        "мусор",
        "ftp://10.0.0.1:1080",
        "socks5://10.0.0.1",
        "mtproxy://proxy.example.com:2002",
        "mtproxy://proxy.example.com:2002?secret=",
    ],
)
def test_broken_proxy_falls_back_to_direct(raw: str) -> None:
    """Опечатка в адресе не должна валить сервис — идём напрямую."""
    assert parse_proxy(raw) is None


def test_describe_hides_credentials() -> None:
    """Строка прокси попадает в логи, пароль в ней светиться не должен."""
    config = parse_proxy("socks5://user:secret@10.0.0.1:1080")
    assert config is not None
    described = config.describe()
    assert "secret" not in described
    assert "user" not in described
    assert "10.0.0.1:1080" in described
