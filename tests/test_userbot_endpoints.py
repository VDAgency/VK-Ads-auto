"""Реестр точек подключения: разбор env и порядок перебора.

Главный инвариант, который здесь закрывается: перебор НИКОГДА не меняет `dc_id`.
Ключ авторизации привязан к дата-центру, и смена `dc_id` у живой сессии разлогинит
оператора молча.
"""

from __future__ import annotations

import pytest
from userbot.endpoints import (
    DEFAULT_AUTH_DC_ORDER,
    DEFAULT_DC_IPS,
    DEFAULT_PORTS,
    Endpoint,
    EndpointResolver,
    Transport,
    parse_dc_order,
    parse_endpoints,
    parse_ports,
    parse_transport,
    parse_transports,
)

_PROD_PINS = "2:149.154.167.51:5222,4:149.154.167.91:5222"


# --- разбор env ----------------------------------------------------------------


def test_parse_endpoints_reads_production_pins() -> None:
    pinned = parse_endpoints(_PROD_PINS)
    assert pinned[2] == [Endpoint(dc_id=2, ip="149.154.167.51", port=5222)]
    assert pinned[4] == [Endpoint(dc_id=4, ip="149.154.167.91", port=5222)]


def test_parse_endpoints_reads_transport() -> None:
    pinned = parse_endpoints("4:149.154.167.91:5222:obfuscated")
    assert pinned[4][0].transport is Transport.OBFUSCATED


def test_pin_without_transport_takes_configured_one() -> None:
    """Иначе пины молча отменяли бы настройку USERBOT_TRANSPORT."""
    pinned = parse_endpoints(_PROD_PINS, Transport.OBFUSCATED)
    assert pinned[4][0].transport is Transport.OBFUSCATED


def test_explicit_transport_in_pin_wins_over_default() -> None:
    pinned = parse_endpoints("4:149.154.167.91:5222:abridged", Transport.OBFUSCATED)
    assert pinned[4][0].transport is Transport.ABRIDGED


@pytest.mark.parametrize(
    "raw", ["", "мусор", "4:149.154.167.91", "x:1.2.3.4:443", "4:1.2.3.4:порт", "4::443"]
)
def test_parse_endpoints_skips_broken_entries(raw: str) -> None:
    """Опечатка в env не должна валить сервис — запись просто пропускается."""
    assert parse_endpoints(raw) == {}


def test_parse_endpoints_keeps_good_entries_next_to_broken() -> None:
    pinned = parse_endpoints("мусор,4:149.154.167.91:5222")
    assert list(pinned) == [4]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("443,5222,80", (443, 5222, 80)),
        ("5222", (5222,)),
        ("", DEFAULT_PORTS),
        ("мусор", DEFAULT_PORTS),
        ("443,443,5222", (443, 5222)),
        ("0,70000,443", (443,)),
    ],
)
def test_parse_ports(raw: str, expected: tuple[int, ...]) -> None:
    assert parse_ports(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,5,3,2,4", (1, 5, 3, 2, 4)),
        ("4", (4,)),
        ("", DEFAULT_AUTH_DC_ORDER),
        ("9,4", (4,)),
        ("1,1,5", (1, 5)),
    ],
)
def test_parse_dc_order(raw: str, expected: tuple[int, ...]) -> None:
    assert parse_dc_order(raw) == expected


def test_parse_transport_falls_back_to_full() -> None:
    assert parse_transport("obfuscated") is Transport.OBFUSCATED
    assert parse_transport("  FULL ") is Transport.FULL
    assert parse_transport("несуществующий") is Transport.FULL


def test_parse_transports_skips_unknown() -> None:
    assert parse_transports("obfuscated,мусор,abridged") == (
        Transport.OBFUSCATED,
        Transport.ABRIDGED,
    )


# --- порядок перебора ----------------------------------------------------------


def test_candidates_never_change_dc_id() -> None:
    """Инвариант: смена dc_id обнулила бы ключ авторизации оператора."""
    resolver = EndpointResolver(pinned=parse_endpoints(_PROD_PINS))
    chain = resolver.candidates(4)
    assert chain, "цепочка не должна быть пустой"
    assert {endpoint.dc_id for endpoint in chain} == {4}


def test_candidates_ignore_pins_of_other_dc() -> None:
    """Пин с чужим dc_id — это опечатка в env; в цепочку он попасть не должен."""
    resolver = EndpointResolver(pinned={4: [Endpoint(dc_id=2, ip="1.2.3.4", port=443)]})
    assert all(endpoint.dc_id == 4 for endpoint in resolver.candidates(4))


def test_candidates_put_pinned_endpoint_first() -> None:
    resolver = EndpointResolver(pinned=parse_endpoints(_PROD_PINS))
    assert resolver.candidates(4)[0] == Endpoint(dc_id=4, ip="149.154.167.91", port=5222)


def test_candidates_put_cached_endpoint_before_pins() -> None:
    cached = Endpoint(dc_id=4, ip="149.154.167.91", port=80, transport=Transport.OBFUSCATED)
    resolver = EndpointResolver(
        pinned=parse_endpoints(_PROD_PINS), cache_lookup=lambda _sender: cached
    )
    assert resolver.candidates(4, sender_id=111)[0] == cached


def test_candidates_include_session_endpoint() -> None:
    known = Endpoint(dc_id=4, ip="149.154.167.91", port=5222)
    resolver = EndpointResolver()
    assert known in resolver.candidates(4, session_endpoint=known)


def test_candidates_are_deduplicated() -> None:
    resolver = EndpointResolver(pinned=parse_endpoints(_PROD_PINS))
    chain = resolver.candidates(4)
    assert len(chain) == len(set(chain))


def test_candidates_cover_all_ports_and_transports() -> None:
    resolver = EndpointResolver(
        ports=(443, 5222), transport=Transport.FULL, transport_fallbacks=(Transport.OBFUSCATED,)
    )
    chain = resolver.candidates(1)
    assert {(e.port, e.transport) for e in chain} == {
        (443, Transport.FULL),
        (5222, Transport.FULL),
        (443, Transport.OBFUSCATED),
        (5222, Transport.OBFUSCATED),
    }


def test_proxy_candidates_come_last() -> None:
    """Сначала пробуем напрямую: прокси дороже и медленнее."""
    resolver = EndpointResolver(ports=(443,), proxy_configured=True)
    chain = resolver.candidates(1)
    assert not chain[0].via_proxy
    assert chain[-1].via_proxy
    first_proxy = next(i for i, e in enumerate(chain) if e.via_proxy)
    assert all(not e.via_proxy for e in chain[:first_proxy])


def test_no_proxy_candidates_when_proxy_not_configured() -> None:
    resolver = EndpointResolver(ports=(443,))
    assert all(not endpoint.via_proxy for endpoint in resolver.candidates(1))


# --- стартовые точки нового логина ---------------------------------------------


def test_auth_candidates_follow_configured_dc_order() -> None:
    """Начинаем с полностью открытых дата-центров: домашний назначит Telegram."""
    resolver = EndpointResolver(ports=(443,), auth_dc_order=(1, 5, 3, 2, 4))
    assert [endpoint.dc_id for endpoint in resolver.auth_candidates()] == [1, 5, 3, 2, 4]


def test_auth_candidates_use_pins() -> None:
    resolver = EndpointResolver(
        pinned=parse_endpoints(_PROD_PINS), ports=(443,), auth_dc_order=(4,)
    )
    assert resolver.auth_candidates()[0].port == 5222


def test_auth_candidates_are_not_empty_for_default_config() -> None:
    assert EndpointResolver().auth_candidates()


# --- предпочтительная точка (её подставляет PinnedStringSession) ----------------


def test_preferred_replaces_default_dc2_address() -> None:
    """Ровно тот случай, из-за которого не проходила новая авторизация."""
    resolver = EndpointResolver(pinned=parse_endpoints(_PROD_PINS))
    endpoint = resolver.preferred(2, fallback_ip="149.154.167.51", fallback_port=443)
    assert (endpoint.ip, endpoint.port) == ("149.154.167.51", 5222)


def test_preferred_replaces_migrated_dc4_address() -> None:
    """После PhoneMigrateError Telethon приносит :443 — подменяем на рабочий порт."""
    resolver = EndpointResolver(pinned=parse_endpoints(_PROD_PINS))
    endpoint = resolver.preferred(4, fallback_ip="149.154.167.91", fallback_port=443)
    assert (endpoint.ip, endpoint.port) == ("149.154.167.91", 5222)


def test_preferred_uses_builtin_address_without_pins() -> None:
    endpoint = EndpointResolver(ports=(5222,)).preferred(4, fallback_ip="x", fallback_port=443)
    assert (endpoint.ip, endpoint.port) == (DEFAULT_DC_IPS[4], 5222)


def test_preferred_trusts_library_for_unknown_dc() -> None:
    """Telegram может завести новый дата-центр — не выдумываем за него адрес."""
    endpoint = EndpointResolver().preferred(9, fallback_ip="1.2.3.4", fallback_port=443)
    assert (endpoint.dc_id, endpoint.ip, endpoint.port) == (9, "1.2.3.4", 443)
