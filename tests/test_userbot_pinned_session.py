"""`PinnedStringSession` — перехват адреса дата-центра.

Регресс на прод-инцидент: Telethon подставляет адреса с портом 443 в двух местах —
жёсткой константой для новой сессии и после `PhoneMigrateError`. Оба закрываются
переопределённым `set_dc`. Проверяем, что `dc_id` при этом не меняется: иначе
обнулится ключ авторизации.
"""

from __future__ import annotations

from telethon.sessions import StringSession
from userbot.endpoints import Endpoint, EndpointResolver, Transport, parse_endpoints
from userbot.pinned_session import PinnedStringSession, session_endpoint

_PROD_PINS = "2:149.154.167.51:5222,4:149.154.167.91:5222"


def _resolver() -> EndpointResolver:
    return EndpointResolver(pinned=parse_endpoints(_PROD_PINS))


def test_default_dc2_address_is_replaced() -> None:
    """Telethon зовёт set_dc(2, 149.154.167.51, 443) для любой пустой сессии."""
    session = PinnedStringSession(None, _resolver())
    session.set_dc(2, "149.154.167.51", 443)
    assert (session.server_address, session.port) == ("149.154.167.51", 5222)


def test_migrated_dc4_address_is_replaced() -> None:
    """Эмуляция _switch_dc после PhoneMigrateError: адрес из help.getConfig с :443."""
    session = PinnedStringSession(None, _resolver())
    session.set_dc(4, "149.154.167.91", 443)
    assert (session.server_address, session.port) == ("149.154.167.91", 5222)
    assert session.dc_id == 4


def test_dc_id_is_never_changed() -> None:
    session = PinnedStringSession(None, _resolver())
    session.set_dc(4, "149.154.167.91", 443)
    assert session.dc_id == 4


def test_applied_endpoint_is_exposed_for_diagnostics() -> None:
    session = PinnedStringSession(None, _resolver())
    session.set_dc(4, "149.154.167.91", 443)
    assert session.applied == Endpoint(dc_id=4, ip="149.154.167.91", port=5222)


def test_unknown_dc_keeps_library_address() -> None:
    session = PinnedStringSession(None, EndpointResolver())
    session.set_dc(9, "1.2.3.4", 443)
    assert (session.dc_id, session.server_address, session.port) == (9, "1.2.3.4", 443)


def test_set_dc_does_not_drop_auth_key() -> None:
    """Ключ привязан к дата-центру: подмена адреса в его пределах его не трогает."""
    session = PinnedStringSession(None, _resolver())
    session.set_dc(4, "149.154.167.91", 443)
    session.auth_key = object()
    session.set_dc(4, "149.154.167.91", 443)
    assert session.auth_key is not None


def test_saved_string_carries_replaced_endpoint() -> None:
    """Сохранённая сессия сама несёт рабочую точку — после рестарта её и возьмут."""
    session = PinnedStringSession(None, _resolver())
    session.set_dc(4, "149.154.167.91", 443)

    class _Key:
        key = b"k" * 256

    session.auth_key = _Key()
    restored = StringSession(session.save())
    assert (restored.dc_id, restored.server_address, restored.port) == (
        4,
        "149.154.167.91",
        5222,
    )


# --- точка из сохранённой строки сессии ----------------------------------------


def test_session_endpoint_reads_saved_string() -> None:
    session = PinnedStringSession(None, _resolver())
    session.set_dc(4, "149.154.167.91", 443)

    class _Key:
        key = b"k" * 256

    session.auth_key = _Key()
    endpoint = session_endpoint(session.save(), _resolver())
    assert endpoint == Endpoint(dc_id=4, ip="149.154.167.91", port=5222, transport=Transport.FULL)


def test_session_endpoint_returns_none_for_empty_and_broken() -> None:
    resolver = _resolver()
    assert session_endpoint(None, resolver) is None
    assert session_endpoint("", resolver) is None
    assert session_endpoint("не-строка-сессии", resolver) is None
