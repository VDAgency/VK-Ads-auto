"""Реестр точек подключения к дата-центрам Telegram (spec 2026-07-31 §4.1).

Зачем это нужно. Telethon подключается к дата-центру по адресу, который берёт из двух
мест: жёсткой константы `DEFAULT_IPV4_IP = '149.154.167.51'` для новой сессии и ответа
`help.getConfig()` при миграции. Оба варианта используют порт 443, а на нашем сервере
443 и 80 закрыты у DC2 и DC4 — при том, что порт 5222 у них открыт (docs/BOT_GAPS.md §0).
Поэтому адрес дата-центра выбираем мы, а не библиотека.

Инвариант, на котором всё держится: перебор НИКОГДА не меняет `dc_id`. Ключ авторизации
привязан к конкретному дата-центру, и смена `dc_id` у живой сессии его обнуляет —
оператор молча разлогинится. Перебираются только адрес, порт, транспорт и прокси.

IP дата-центров — публичная информация (зеркало `help.getConfig`), секретов здесь нет.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

logger = logging.getLogger(__name__)


class Transport(StrEnum):
    """Транспорт MTProto. Значение = то, что пишут в env."""

    FULL = "full"
    ABRIDGED = "abridged"
    INTERMEDIATE = "intermediate"
    OBFUSCATED = "obfuscated"
    MTPROXY = "mtproxy"


# Адреса production-дата-центров Telegram. Источник — help.getConfig (зеркалится
# в документации Pyrogram/Hydrogram). Telegram оставляет за собой право их менять,
# поэтому это разумный дефолт, а не догма: перекрывается USERBOT_DC_ENDPOINTS.
DEFAULT_DC_IPS: Final[dict[int, str]] = {
    1: "149.154.175.53",
    2: "149.154.167.51",
    3: "149.154.175.100",
    4: "149.154.167.91",
    5: "91.108.56.130",
}

# Порядок портов по умолчанию. 443 первым — он основной и работает там, где нет
# фильтрации; 5222 вторым — именно он открыт у DC2/DC4 с нашего сервера.
DEFAULT_PORTS: Final[tuple[int, ...]] = (443, 5222, 80)

# Порядок дата-центров для НОВОГО логина. Начинаем с полностью открытых: домашний DC
# всё равно назначит сам Telegram через PhoneMigrateError.
DEFAULT_AUTH_DC_ORDER: Final[tuple[int, ...]] = (1, 5, 3, 2, 4)


@dataclass(frozen=True, slots=True)
class Endpoint:
    """Одна точка подключения: куда идём и каким транспортом."""

    dc_id: int
    ip: str
    port: int
    transport: Transport = Transport.FULL
    via_proxy: bool = False

    def label(self) -> str:
        """Человекочитаемая метка для логов и экрана диагностики."""
        proxy = " +proxy" if self.via_proxy else ""
        return f"dc{self.dc_id} {self.ip}:{self.port}/{self.transport.value}{proxy}"


def parse_ports(raw: str) -> tuple[int, ...]:
    """Разобрать `USERBOT_DC_PORTS` («443,5222,80»). Пусто/мусор → дефолт."""
    ports: list[int] = []
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        try:
            port = int(item)
        except ValueError:
            logger.warning("USERBOT_DC_PORTS: пропускаю нечисловой порт %r", item)
            continue
        if not 1 <= port <= 65535:
            logger.warning("USERBOT_DC_PORTS: порт %s вне диапазона", port)
            continue
        if port not in ports:
            ports.append(port)
    return tuple(ports) or DEFAULT_PORTS


def parse_dc_order(raw: str) -> tuple[int, ...]:
    """Разобрать `USERBOT_AUTH_DC_ORDER` («1,5,3,2,4»). Пусто/мусор → дефолт."""
    order: list[int] = []
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        try:
            dc_id = int(item)
        except ValueError:
            logger.warning("USERBOT_AUTH_DC_ORDER: пропускаю %r", item)
            continue
        if dc_id not in DEFAULT_DC_IPS:
            logger.warning("USERBOT_AUTH_DC_ORDER: неизвестный дата-центр %s", dc_id)
            continue
        if dc_id not in order:
            order.append(dc_id)
    return tuple(order) or DEFAULT_AUTH_DC_ORDER


def parse_transport(raw: str) -> Transport:
    """Разобрать имя транспорта. Неизвестное значение → `full` с предупреждением."""
    try:
        return Transport(raw.strip().lower())
    except ValueError:
        logger.warning("USERBOT_TRANSPORT: неизвестный транспорт %r, беру full", raw)
        return Transport.FULL


def parse_transports(raw: str) -> tuple[Transport, ...]:
    """Разобрать список транспортов через запятую (для фолбэков)."""
    result: list[Transport] = []
    for chunk in raw.split(","):
        item = chunk.strip().lower()
        if not item:
            continue
        try:
            transport = Transport(item)
        except ValueError:
            logger.warning("USERBOT_TRANSPORT_FALLBACKS: пропускаю %r", item)
            continue
        if transport not in result:
            result.append(transport)
    return tuple(result)


def parse_endpoints(
    raw: str, default_transport: Transport = Transport.FULL
) -> dict[int, list[Endpoint]]:
    """Разобрать `USERBOT_DC_ENDPOINTS`: `dc:ip:port[:transport]` через запятую.

    Пример: `2:149.154.167.51:5222,4:149.154.167.91:5222`.
    Транспорт в записи необязателен: без него берётся основной из `USERBOT_TRANSPORT`,
    иначе пины молча отменяли бы настройку транспорта.
    Нераспознанные записи пропускаются с предупреждением — одна опечатка в env
    не должна валить сервис целиком.
    """
    pinned: dict[int, list[Endpoint]] = {}
    for chunk in raw.split(","):
        item = chunk.strip()
        if not item:
            continue
        parts = item.split(":")
        if len(parts) not in (3, 4):
            logger.warning("USERBOT_DC_ENDPOINTS: непонятная запись %r", item)
            continue
        try:
            dc_id, ip, port = int(parts[0]), parts[1].strip(), int(parts[2])
        except ValueError:
            logger.warning("USERBOT_DC_ENDPOINTS: непонятная запись %r", item)
            continue
        if not ip:
            logger.warning("USERBOT_DC_ENDPOINTS: пустой адрес в %r", item)
            continue
        transport = parse_transport(parts[3]) if len(parts) == 4 else default_transport
        endpoint = Endpoint(dc_id=dc_id, ip=ip, port=port, transport=transport)
        pinned.setdefault(dc_id, []).append(endpoint)
    return pinned


class EndpointResolver:
    """Выбор точек подключения: одна предпочтительная и полная цепочка перебора.

    `pinned` (из env) перекрывает встроенные адреса, `cache` — последняя точка, на
    которой подключение реально удалось. Прокси добавляется последним шагом цепочки:
    сначала пробуем напрямую, он дороже и медленнее.
    """

    def __init__(
        self,
        *,
        pinned: dict[int, list[Endpoint]] | None = None,
        ports: tuple[int, ...] = DEFAULT_PORTS,
        transport: Transport = Transport.FULL,
        transport_fallbacks: tuple[Transport, ...] = (),
        auth_dc_order: tuple[int, ...] = DEFAULT_AUTH_DC_ORDER,
        proxy_configured: bool = False,
        cache_lookup: Callable[[int], Endpoint | None] | None = None,
    ) -> None:
        self._pinned = pinned or {}
        self._ports = ports
        self._transports = (transport, *(t for t in transport_fallbacks if t != transport))
        self._auth_dc_order = auth_dc_order
        self._proxy_configured = proxy_configured
        # Инъекция, а не импорт EndpointCache: реестр не должен знать про хранилище.
        self._cache_lookup = cache_lookup

    @property
    def default_transport(self) -> Transport:
        """Основной транспорт — им размечаются точки, пришедшие извне реестра."""
        return self._transports[0]

    def _cached(self, sender_id: int | None) -> Endpoint | None:
        if sender_id is None or self._cache_lookup is None:
            return None
        return self._cache_lookup(sender_id)

    def preferred(self, dc_id: int, *, fallback_ip: str, fallback_port: int) -> Endpoint:
        """Лучшая известная точка дата-центра — то, чем подменяем адрес Telethon.

        `fallback_*` — то, что предложила библиотека: используем, если своих данных
        по этому дата-центру нет (например, Telegram завёл новый DC).
        """
        for endpoint in self._pinned.get(dc_id, ()):
            return endpoint
        transport = self._transports[0]
        ip = DEFAULT_DC_IPS.get(dc_id)
        if ip is None:
            # Неизвестный дата-центр (Telegram завёл новый) — доверяем библиотеке.
            return Endpoint(dc_id=dc_id, ip=fallback_ip, port=fallback_port, transport=transport)
        return Endpoint(dc_id=dc_id, ip=ip, port=self._ports[0], transport=transport)

    def candidates(
        self,
        dc_id: int,
        *,
        session_endpoint: Endpoint | None = None,
        sender_id: int | None = None,
    ) -> list[Endpoint]:
        """Цепочка перебора для дата-центра `dc_id`, в порядке убывания шансов.

        Порядок: кэш → точка из строки сессии → пины из env → встроенные адреса ×
        порты × транспорты → то же через прокси. `dc_id` во всех элементах один и тот
        же — менять его у живой сессии нельзя (обнулит ключ авторизации).
        """
        chain: list[Endpoint] = []

        def add(endpoint: Endpoint) -> None:
            if endpoint.dc_id != dc_id:  # инвариант, страхуемся от опечатки в env
                return
            if endpoint not in chain:
                chain.append(endpoint)

        cached = self._cached(sender_id)
        if cached is not None:
            add(cached)
        if session_endpoint is not None:
            add(session_endpoint)
        for endpoint in self._pinned.get(dc_id, ()):
            add(endpoint)

        ip = DEFAULT_DC_IPS.get(dc_id)
        if ip is not None:
            for transport in self._transports:
                for port in self._ports:
                    add(Endpoint(dc_id=dc_id, ip=ip, port=port, transport=transport))

        if self._proxy_configured:
            # Прокси последним: сначала пробуем напрямую. Транспорт заменяем на
            # mtproxy-совместимый только там, где это задано конфигом прокси, —
            # решает вызывающая сторона через parse_proxy().
            direct = list(chain)
            for endpoint in direct:
                proxied = Endpoint(
                    dc_id=endpoint.dc_id,
                    ip=endpoint.ip,
                    port=endpoint.port,
                    transport=endpoint.transport,
                    via_proxy=True,
                )
                if proxied not in chain:
                    chain.append(proxied)
        return chain

    def auth_candidates(self) -> list[Endpoint]:
        """Стартовые точки для НОВОГО логина (сессия пустая, ключа ещё нет).

        Здесь смена `dc_id` безопасна: привязывать нечего. Домашний дата-центр
        оператора Telegram назначит сам через `PhoneMigrateError`.
        """
        chain: list[Endpoint] = []
        for dc_id in self._auth_dc_order:
            for endpoint in self._pinned.get(dc_id, ()):
                if endpoint not in chain:
                    chain.append(endpoint)
            ip = DEFAULT_DC_IPS.get(dc_id)
            if ip is None:
                continue
            for transport in self._transports:
                for port in self._ports:
                    endpoint = Endpoint(dc_id=dc_id, ip=ip, port=port, transport=transport)
                    if endpoint not in chain:
                        chain.append(endpoint)
        return chain
