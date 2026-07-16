"""Тесты rate-лимитера приёма брифа (`core.api.rate_limit`)."""

from __future__ import annotations

from core.api.rate_limit import SlidingWindowLimiter


def test_allows_up_to_limit() -> None:
    limiter = SlidingWindowLimiter(max_requests=3, window_seconds=60.0)
    assert all(limiter.check("ip1", now=t) for t in (0.0, 1.0, 2.0))


def test_blocks_over_limit() -> None:
    limiter = SlidingWindowLimiter(max_requests=3, window_seconds=60.0)
    for t in (0.0, 1.0, 2.0):
        limiter.check("ip1", now=t)
    assert limiter.check("ip1", now=3.0) is False


def test_window_slides() -> None:
    limiter = SlidingWindowLimiter(max_requests=2, window_seconds=10.0)
    limiter.check("ip1", now=0.0)
    limiter.check("ip1", now=1.0)
    assert limiter.check("ip1", now=2.0) is False
    # После выхода первых хитов за окно — снова можно.
    assert limiter.check("ip1", now=12.0) is True


def test_keys_are_independent() -> None:
    limiter = SlidingWindowLimiter(max_requests=1, window_seconds=60.0)
    assert limiter.check("ip1", now=0.0) is True
    assert limiter.check("ip2", now=0.0) is True
    assert limiter.check("ip1", now=1.0) is False
