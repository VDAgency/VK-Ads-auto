"""Разбор строки прокси в аргумент `proxy=` для Telethon (spec 2026-07-31 §5).

Поддерживаются две формы:

- `socks5://user:pass@host:port` (а также `socks4://`, `http://`) — обычный прокси
  через python-socks;
- `mtproxy://host:port?secret=hex` — MTProxy Telegram; для него Telethon требует ещё
  и особый транспорт, поэтому разбор возвращает его вместе с адресом.

Пусто → `None`: работаем напрямую. Строка может содержать пароль, поэтому в логи
попадает только хост и порт.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from userbot.endpoints import Transport

logger = logging.getLogger(__name__)

# Схема из URL → значение proxy_type для python-socks.
_SOCKS_SCHEMES = {"socks5": "socks5", "socks4": "socks4", "http": "http"}


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    """Готовый аргумент `proxy=` для `TelegramClient` и транспорт, если он навязан."""

    # dict для python-socks либо кортеж (host, port, secret) для MTProxy.
    value: dict[str, object] | tuple[str, int, str]
    transport: Transport | None = None

    def describe(self) -> str:
        """Безопасное для логов описание — без логина и пароля."""
        if isinstance(self.value, tuple):
            host, port, _ = self.value
            return f"mtproxy {host}:{port}"
        return f"{self.value.get('proxy_type')} {self.value.get('addr')}:{self.value.get('port')}"


def parse_proxy(raw: str) -> ProxyConfig | None:
    """Разобрать строку прокси. Пусто или мусор → `None` (идём напрямую)."""
    value = raw.strip()
    if not value:
        return None
    parsed = urlparse(value)
    scheme = parsed.scheme.lower()

    if scheme == "mtproxy":
        if not parsed.hostname or not parsed.port:
            logger.warning("USERBOT_PROXY: в mtproxy-адресе нет хоста или порта")
            return None
        secrets = parse_qs(parsed.query).get("secret", [])
        if not secrets or not secrets[0]:
            logger.warning("USERBOT_PROXY: у mtproxy не задан secret")
            return None
        return ProxyConfig(
            value=(parsed.hostname, parsed.port, secrets[0]),
            # MTProxy работает только со своим транспортом — навязываем его.
            transport=Transport.MTPROXY,
        )

    proxy_type = _SOCKS_SCHEMES.get(scheme)
    if proxy_type is None:
        logger.warning("USERBOT_PROXY: неизвестная схема %r — игнорирую прокси", scheme)
        return None
    if not parsed.hostname or not parsed.port:
        logger.warning("USERBOT_PROXY: в адресе нет хоста или порта")
        return None

    config: dict[str, object] = {
        "proxy_type": proxy_type,
        "addr": parsed.hostname,
        "port": parsed.port,
        # Резолвим имена на стороне прокси: иначе DNS уйдёт мимо туннеля.
        "rdns": True,
    }
    if parsed.username:
        config["username"] = parsed.username
    if parsed.password:
        config["password"] = parsed.password
    return ProxyConfig(value=config)
