"""Параметры, с которыми создаётся настоящий `TelegramClient`.

Раньше `default_client_factory` не был покрыт вообще — а именно там жила причина
прод-инцидента: клиент собирался тремя аргументами, без единого параметра
устойчивости. Тест ловит и обратную ситуацию: если Telethon переименует параметр,
сломается сборка, а не прод.
"""

from __future__ import annotations

from typing import Any

import pytest
import telethon
from pydantic import SecretStr
from userbot.config import UserbotSettings
from userbot.endpoints import Endpoint, EndpointResolver, Transport
from userbot.pinned_session import PinnedStringSession
from userbot.telethon_client import default_client_factory


class _Recorder:
    """Подставной TelegramClient: запоминает, с чем его позвали."""

    last_args: tuple[Any, ...] = ()
    last_kwargs: dict[str, Any] = {}

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        type(self).last_args = args
        type(self).last_kwargs = kwargs


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> type[_Recorder]:
    monkeypatch.setattr(telethon, "TelegramClient", _Recorder)
    return _Recorder


def _settings(**overrides: Any) -> UserbotSettings:
    base: dict[str, Any] = {
        "api_id": 12345,
        "api_hash": SecretStr("hash"),
        "secret_key": SecretStr(""),
        "connect_timeout": 8,
    }
    base.update(overrides)
    return UserbotSettings(**base)


def _build(recorder: type[_Recorder], **overrides: Any) -> dict[str, Any]:
    factory = default_client_factory(_settings(**overrides), EndpointResolver())
    factory(None, Endpoint(dc_id=1, ip="149.154.175.53", port=443))
    return recorder.last_kwargs


def test_session_is_pinned(recorder: type[_Recorder]) -> None:
    """Без нашей сессии Telethon уйдёт на жёстко зашитый недоступный адрес."""
    _build(recorder)
    assert isinstance(recorder.last_args[0], PinnedStringSession)


def test_single_connection_attempt(recorder: type[_Recorder]) -> None:
    """Пять ретраев одной мёртвой точки — это и есть 54 секунды зависания /health."""
    kwargs = _build(recorder)
    assert kwargs["connection_retries"] == 1
    assert kwargs["retry_delay"] == 0


def test_flood_sleep_is_disabled(recorder: type[_Recorder]) -> None:
    """Дефолт 60 заставляет молча спать внутри запроса — вызывающий видит таймаут."""
    assert _build(recorder)["flood_sleep_threshold"] == 0


def test_updates_are_not_received(recorder: type[_Recorder]) -> None:
    """Апдейты сервису не нужны: обработчиков нет, очередь копить незачем."""
    kwargs = _build(recorder)
    assert kwargs["receive_updates"] is False
    assert kwargs["catch_up"] is False


def test_device_fingerprint_is_explicit(recorder: type[_Recorder]) -> None:
    """Дефолты меняются при обновлении ядра и Telethon — аккаунт «переезжает»."""
    kwargs = _build(recorder)
    assert kwargs["device_model"] == "VK Ads Auto"
    assert kwargs["system_version"] == "Ubuntu 24.04"
    assert kwargs["app_version"] == "1.0"
    assert kwargs["lang_code"] == "ru"
    assert kwargs["system_lang_code"] == "ru"


def test_timeout_comes_from_settings(recorder: type[_Recorder]) -> None:
    assert _build(recorder, connect_timeout=3)["timeout"] == 3


def test_ipv6_disabled(recorder: type[_Recorder]) -> None:
    """На сервере IPv6 нет; рассинхрон флага сбрасывает адрес на дефолтный."""
    assert _build(recorder)["use_ipv6"] is False


def test_transport_follows_endpoint(recorder: type[_Recorder]) -> None:
    factory = default_client_factory(_settings(), EndpointResolver())
    factory(
        None,
        Endpoint(dc_id=1, ip="149.154.175.53", port=443, transport=Transport.OBFUSCATED),
    )
    assert recorder.last_kwargs["connection"] is telethon.connection.ConnectionTcpObfuscated


def test_proxy_used_only_for_proxy_endpoints(recorder: type[_Recorder]) -> None:
    """Прокси — последний шаг цепочки: прямые точки должны идти без него."""
    from userbot.proxy import parse_proxy

    proxy = parse_proxy("socks5://10.0.0.1:1080")
    factory = default_client_factory(_settings(), EndpointResolver(), proxy)

    factory(None, Endpoint(dc_id=1, ip="149.154.175.53", port=443))
    assert recorder.last_kwargs["proxy"] is None

    factory(None, Endpoint(dc_id=1, ip="149.154.175.53", port=443, via_proxy=True))
    assert recorder.last_kwargs["proxy"] is not None
