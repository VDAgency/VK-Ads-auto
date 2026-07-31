"""Поллер сессий юзербота: кеш состояния и уведомления операторам.

Ключевые требования: одно уведомление на переход (а не на каждый опрос), напоминание
раз в сутки, пока не починено, и разные тексты для «сеть не пускает» и «разлогинен» —
советовать перепривязку при сетевой блокировке вредно, она не поможет.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from aiogram import Bot
from bot import userbot_watch
from bot.api_client import UserbotHealth, UserbotUnavailable

_DAY = 24 * 3600.0


def teardown_function() -> None:
    userbot_watch.reset()


class _FakeBot:
    """Собирает отправленные уведомления: (кому, текст)."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def _health(**by_sender: UserbotHealth) -> Any:
    async def fake_all() -> dict[int, UserbotHealth]:
        return {int(key): value for key, value in by_sender.items()}

    return fake_all


def _ready(phone: str | None = "79990001122") -> UserbotHealth:
    return UserbotHealth(authorized=True, phone=phone)


def _dead() -> UserbotHealth:
    return UserbotHealth(authorized=False)


def _unreachable() -> UserbotHealth:
    return UserbotHealth(authorized=False, error="unreachable")


def _set(monkeypatch: pytest.MonkeyPatch, fake: Any) -> None:
    monkeypatch.setattr("bot.api_client.userbot_health_all", fake)


def _operators(monkeypatch: pytest.MonkeyPatch, *ids: int) -> None:
    from config.settings import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "operator_telegram_ids", frozenset(ids))


# --- кеш состояния -------------------------------------------------------------


def test_unknown_before_first_poll() -> None:
    assert userbot_watch.is_authorized(111) is None


def test_refresh_populates_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, _health(**{"111": _ready(), "222": _dead()}))
    asyncio.run(userbot_watch.refresh_once())

    assert userbot_watch.is_authorized(111) is True
    assert userbot_watch.is_authorized(222) is False
    # Оператор без сессии при известном состоянии — не авторизован (баннер нужен).
    assert userbot_watch.is_authorized(333) is False


def test_unavailable_resets_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, _health(**{"111": _ready()}))
    asyncio.run(userbot_watch.refresh_once())
    assert userbot_watch.is_authorized(111) is True

    async def boom() -> dict[int, UserbotHealth]:
        raise UserbotUnavailable("down")

    _set(monkeypatch, boom)
    asyncio.run(userbot_watch.refresh_once())
    # Кеш не «врёт»: состояние неизвестно, а не «всё ещё авторизован».
    assert userbot_watch.is_authorized(111) is None


def test_unreachable_is_not_the_same_as_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, _health(**{"111": _unreachable()}))
    asyncio.run(userbot_watch.refresh_once())
    assert userbot_watch.is_authorized(111) is False
    assert userbot_watch.is_unreachable(111) is True


# --- баннер для /send_brief ----------------------------------------------------


def test_banner_silent_before_first_poll() -> None:
    assert userbot_watch.banner_for(111) is None


def test_banner_silent_for_working_session(monkeypatch: pytest.MonkeyPatch) -> None:
    _set(monkeypatch, _health(**{"111": _ready()}))
    asyncio.run(userbot_watch.refresh_once())
    assert userbot_watch.banner_for(111) is None


def test_banner_explains_network_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Раньше при недоступности баннер не показывался вовсе — оператор не знал ничего."""
    _set(monkeypatch, _health(**{"111": _unreachable()}))
    asyncio.run(userbot_watch.refresh_once())
    banner = userbot_watch.banner_for(111)
    assert banner is not None
    assert "Перепривязка не поможет" in banner


def test_banner_reports_service_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom() -> dict[int, UserbotHealth]:
        raise UserbotUnavailable("down")

    _set(monkeypatch, boom)
    asyncio.run(userbot_watch.refresh_once())
    assert userbot_watch.banner_for(111) == userbot_watch.SERVICE_DOWN_MESSAGE


# --- уведомления ---------------------------------------------------------------


def test_first_poll_never_notifies(monkeypatch: pytest.MonkeyPatch) -> None:
    """Рестарт бота не должен выглядеть как авария."""
    _operators(monkeypatch, 111)
    _set(monkeypatch, _health(**{"111": _dead()}))
    bot = _FakeBot()
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=0.0))
    assert bot.sent == []


def test_transition_to_dead_notifies_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    _operators(monkeypatch, 111, 222)
    _set(monkeypatch, _health(**{"111": _ready()}))
    bot = _FakeBot()
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=0.0))

    _set(monkeypatch, _health(**{"111": _dead()}))
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=60.0))

    assert len(bot.sent) == 1
    chat_id, text = bot.sent[0]
    assert chat_id == 111, "пишем владельцу сессии, а не всем подряд"
    assert "/link_userbot" in text


def test_unreachable_notification_does_not_advise_relinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _operators(monkeypatch, 111)
    _set(monkeypatch, _health(**{"111": _ready()}))
    bot = _FakeBot()
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=0.0))

    _set(monkeypatch, _health(**{"111": _unreachable()}))
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=60.0))

    assert "Перепривязка не поможет" in bot.sent[0][1]


def test_repeated_polls_stay_silent_within_a_day(monkeypatch: pytest.MonkeyPatch) -> None:
    _operators(monkeypatch, 111)
    _set(monkeypatch, _health(**{"111": _ready()}))
    bot = _FakeBot()
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=0.0))

    _set(monkeypatch, _health(**{"111": _dead()}))
    for minute in range(1, 20):
        asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=60.0 * minute))

    assert len(bot.sent) == 1, "поток одинаковых сообщений перестают читать"


def test_reminder_comes_once_a_day(monkeypatch: pytest.MonkeyPatch) -> None:
    _operators(monkeypatch, 111)
    _set(monkeypatch, _health(**{"111": _ready()}))
    bot = _FakeBot()
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=0.0))

    _set(monkeypatch, _health(**{"111": _dead()}))
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=60.0))
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=60.0 + _DAY))

    assert len(bot.sent) == 2, "через сутки напоминаем ещё раз"


def test_recovery_notifies_and_resets_reminder(monkeypatch: pytest.MonkeyPatch) -> None:
    _operators(monkeypatch, 111)
    _set(monkeypatch, _health(**{"111": _ready()}))
    bot = _FakeBot()
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=0.0))

    _set(monkeypatch, _health(**{"111": _dead()}))
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=60.0))
    _set(monkeypatch, _health(**{"111": _ready()}))
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=120.0))

    assert bot.sent[-1] == (111, userbot_watch.RECOVERED_MESSAGE)

    # Сломалось снова — уведомление приходит сразу, а не через сутки.
    _set(monkeypatch, _health(**{"111": _dead()}))
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=180.0))
    assert "/link_userbot" in bot.sent[-1][1]


def test_service_down_notifies_all_operators(monkeypatch: pytest.MonkeyPatch) -> None:
    _operators(monkeypatch, 111, 222)
    _set(monkeypatch, _health(**{"111": _ready()}))
    bot = _FakeBot()
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=0.0))

    async def boom() -> dict[int, UserbotHealth]:
        raise UserbotUnavailable("down")

    _set(monkeypatch, boom)
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=60.0))

    assert sorted(chat_id for chat_id, _ in bot.sent) == [111, 222]
    assert all("email" in text for _, text in bot.sent)


def test_service_recovery_is_announced(monkeypatch: pytest.MonkeyPatch) -> None:
    _operators(monkeypatch, 111)

    async def boom() -> dict[int, UserbotHealth]:
        raise UserbotUnavailable("down")

    _set(monkeypatch, boom)
    bot = _FakeBot()
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=0.0))
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=60.0))

    _set(monkeypatch, _health(**{"111": _ready()}))
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=120.0))

    assert bot.sent[-1] == (111, userbot_watch.SERVICE_UP_MESSAGE)


def test_failed_notification_does_not_break_the_poller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Один оператор заблокировал бота — остальные всё равно должны получить."""
    _operators(monkeypatch, 111, 222)
    _set(monkeypatch, _health(**{"111": _ready()}))

    class _PartlyBrokenBot(_FakeBot):
        async def send_message(self, chat_id: int, text: str) -> None:
            if chat_id == 111:
                raise RuntimeError("bot blocked")
            await super().send_message(chat_id, text)

    bot = _PartlyBrokenBot()
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=0.0))

    async def boom() -> dict[int, UserbotHealth]:
        raise UserbotUnavailable("down")

    _set(monkeypatch, boom)
    asyncio.run(userbot_watch.check_once(cast(Bot, bot), now=60.0))

    assert [chat_id for chat_id, _ in bot.sent] == [222]
