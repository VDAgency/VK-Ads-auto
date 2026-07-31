"""Наблюдение за сессиями юзербота: кеш состояния + уведомления операторам.

Раньше поллер хранил один флаг «авторизован», а при недоступности сервиса ставил
«не знаю» — и оператор о поломке не узнавал ниоткуда: баннер в `/send_brief`
сравнивается строго с `False` и при «не знаю» не показывался вовсе.

Теперь различаем три ситуации, потому что лечатся они по-разному:
- сервис юзербота не отвечает — проблема на нашей стороне;
- Telegram недоступен с сервера (`unreachable`) — восстановится само, и перепривязка
  тут НЕ поможет: новая сессия приземлится на тот же дата-центр;
- сессия мертва — вот здесь нужна перепривязка оператором.

Уведомляем владельца сессии адресно: `sender_id` и есть Telegram ID оператора.
Веерная рассылка — только про недоступность сервиса целиком.
"""

from __future__ import annotations

import asyncio
import logging
import time

from aiogram import Bot
from config.settings import get_settings

from bot import api_client
from bot.api_client import UserbotUnavailable

logger = logging.getLogger(__name__)

# Пока состояние плохое, напоминаем раз в сутки: одно сообщение забывается, поток
# сообщений раздражает и его перестают читать.
REMINDER_INTERVAL = 24 * 3600.0

SERVICE_DOWN_MESSAGE = (
    "⚠️ Сервис юзербота не отвечает — отправка брифов в Telegram сейчас не работает. "
    "Брифы можно отправлять на email."
)
SERVICE_UP_MESSAGE = "✅ Сервис юзербота снова отвечает."
UNREACHABLE_MESSAGE = (
    "⚠️ Telegram сейчас недоступен с сервера — брифы от вашего аккаунта не уходят.\n"
    "Перепривязка не поможет: соединение блокируется на стороне сети. Бот продолжает "
    "пробовать сам. Пока отправляйте брифы на email."
)
EXPIRED_MESSAGE = (
    "🔐 Ваш юзер-бот разлогинен — брифы в Telegram уходить не будут.\n"
    "Подключите заново: /link_userbot"
)
RECOVERED_MESSAGE = "✅ Ваш юзер-бот снова на связи."
NOT_LINKED_BANNER = (
    "⚠️ Ваш юзербот не подключён (/link_userbot). Автоотправка в Telegram не "
    "сработает — бот выдаст текст для ручной пересылки."
)

# Состояние сервиса: True/False — знаем, None — ещё не опрашивали.
_service_up: bool | None = None
# Сессии операторов: sender_id → авторизована ли.
_status: dict[int, bool] = {}
# Кого не видно из-за сети (а не из-за разлогина).
_unreachable: set[int] = set()
# Когда последний раз уведомляли. Ключ 0 — сообщение уровня сервиса.
_notified_at: dict[int, float] = {}


def reset() -> None:
    """Сбросить кеш (тесты и перезапуск поллера)."""
    global _service_up
    _service_up = None
    _status.clear()
    _unreachable.clear()
    _notified_at.clear()


async def refresh_once() -> None:
    """Один опрос состояния сессий. Недоступный сервис ≠ мёртвая сессия."""
    global _service_up
    try:
        sessions = await api_client.userbot_health_all()
    except UserbotUnavailable:
        _service_up = False
        _status.clear()
        _unreachable.clear()
        logger.warning("userbot health poll failed: service unavailable")
        return
    _service_up = True
    _status.clear()
    _unreachable.clear()
    for sender_id, state in sessions.items():
        _status[sender_id] = state.authorized
        if state.error == "unreachable":
            _unreachable.add(sender_id)


def is_authorized(sender_id: int) -> bool | None:
    """Авторизована ли сессия оператора; `None` — состояние неизвестно."""
    if _service_up is not True:
        return None
    return _status.get(sender_id, False)


def is_unreachable(sender_id: int) -> bool:
    """Сессию не видно из-за сети, а не потому, что она разлогинена."""
    return sender_id in _unreachable


def banner_for(sender_id: int) -> str | None:
    """Предупреждение для `/send_brief`; `None` — предупреждать не о чем."""
    if _service_up is False:
        return SERVICE_DOWN_MESSAGE
    if _service_up is None:
        return None
    if is_unreachable(sender_id):
        return UNREACHABLE_MESSAGE
    if _status.get(sender_id, False):
        return None
    return NOT_LINKED_BANNER


async def _notify(bot: Bot, chat_id: int, text: str) -> None:
    """Отправить уведомление; недоставка одному не должна валить поллер."""
    try:
        await bot.send_message(chat_id, text)
    except Exception:  # noqa: BLE001 — недоставка не блокер
        logger.warning("userbot_watch: не удалось уведомить %s", chat_id)


def _due(key: int, now: float) -> bool:
    """Пора ли (пере)уведомить: впервые или прошли сутки с прошлого раза."""
    last = _notified_at.get(key)
    return last is None or now - last >= REMINDER_INTERVAL


async def _notify_all(bot: Bot, text: str) -> None:
    for operator_id in sorted(get_settings().operator_telegram_ids):
        await _notify(bot, operator_id, text)


async def check_once(bot: Bot, *, now: float | None = None) -> None:
    """Опрос + уведомления на переходах и напоминания раз в сутки."""
    moment = time.time() if now is None else now
    previous_service = _service_up
    previous_status = dict(_status)
    previous_unreachable = set(_unreachable)
    await refresh_once()

    if _service_up is False:
        if _due(0, moment):
            _notified_at[0] = moment
            await _notify_all(bot, SERVICE_DOWN_MESSAGE)
        return
    service_recovered = previous_service is False
    if service_recovered:
        _notified_at.pop(0, None)
        await _notify_all(bot, SERVICE_UP_MESSAGE)

    # Первый опрос после старта молчит: «мы только что узнали» — не «только что
    # сломалось», иначе рестарт бота выглядел бы как авария.
    if previous_service is None:
        return

    for sender_id, authorized in _status.items():
        was_ok = previous_status.get(sender_id, False) and sender_id not in previous_unreachable
        if authorized and sender_id not in _unreachable:
            # После восстановления сервиса про рабочие сессии молчим: сообщение
            # уровня сервиса уже ушло, второе про то же самое — шум.
            if not was_ok and not service_recovered:
                _notified_at.pop(sender_id, None)
                await _notify(bot, sender_id, RECOVERED_MESSAGE)
            if service_recovered:
                _notified_at.pop(sender_id, None)
            continue
        if not _due(sender_id, moment):
            continue
        _notified_at[sender_id] = moment
        await _notify(
            bot,
            sender_id,
            UNREACHABLE_MESSAGE if sender_id in _unreachable else EXPIRED_MESSAGE,
        )


async def poll_forever(bot: Bot, interval: float = 60.0) -> None:
    """Бесконечный цикл опроса (фоновая задача в bot/main.py)."""
    while True:
        await check_once(bot)
        await asyncio.sleep(interval)
