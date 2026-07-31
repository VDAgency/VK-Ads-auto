"""Обёртка над Telethon-клиентами: auth-флоу, отправка, health (spec §6).

`TelegramClient` инкапсулирован за узким `TelethonProtocol` и создаётся через
инъектируемую фабрику — так тесты подменяют Telethon моком без сети. Строки сессий
шифруются на диске через `SessionStore`; после успешного логина сохраняются.

Сессий несколько — по одной на оператора (`sender_id` = Telegram ID). Клиент
держит реестр подключённых клиентов и незавершённых auth-флоу по sender_id:
два оператора могут авторизовываться и отправлять независимо друг от друга.

Ошибки отправки не пробрасываются наружу как исключения Telethon — конвертируются
в коды §9 через `errors.map_send_error`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, cast

from telethon import errors

from userbot.config import UserbotSettings
from userbot.endpoint_cache import EndpointCache
from userbot.endpoints import Endpoint, EndpointResolver, Transport
from userbot.errors import classify, state_for
from userbot.pinned_session import PinnedStringSession, session_endpoint
from userbot.proxy import ProxyConfig
from userbot.session import SessionStore
from userbot.state import SessionInfo, SessionState, StateRegistry

logger = logging.getLogger(__name__)


class UnreachableError(Exception):
    """Ни одна точка подключения не отозвалась — сеть/дата-центр недоступны.

    Отдельно от ошибок авторизации: это состояние восстанавливается само и НЕ требует
    перепривязки юзербота. Советовать оператору `/link_userbot` здесь вредно — новая
    сессия приземлится на тот же недоступный дата-центр.
    """


class SessionProtocol(Protocol):
    """Сессия Telethon умеет сериализоваться в строку (StringSession.save())."""

    def save(self) -> str: ...


class TelethonProtocol(Protocol):
    """Узкий контракт используемых методов TelegramClient (для мокинга/типизации)."""

    @property
    def session(self) -> SessionProtocol: ...

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    def is_connected(self) -> bool: ...
    async def is_user_authorized(self) -> bool: ...
    async def send_code_request(self, phone: str) -> object: ...
    async def sign_in(
        self,
        phone: str | None = ...,
        code: str | int | None = ...,
        *,
        password: str | None = ...,
        phone_code_hash: str | None = ...,
    ) -> object: ...
    async def sign_in_password(self, password: str) -> object: ...
    async def send_message(self, entity: str, message: str) -> object: ...
    async def get_entity(self, entity: str) -> object: ...
    async def get_me(self) -> object: ...


def _display_name(entity: object) -> str | None:
    """Имя получателя из Telethon-сущности (first + last), или None если пусто."""
    first = getattr(entity, "first_name", None) or ""
    last = getattr(entity, "last_name", None) or ""
    name = f"{first} {last}".strip()
    return name or None


class ClientFactory(Protocol):
    """Фабрика клиента: строка сессии + точка подключения → клиент Telethon."""

    def __call__(self, session_str: str | None, endpoint: Endpoint) -> TelethonProtocol: ...


# Транспорт из реестра → класс соединения Telethon. Импортируются лениво: в тестах
# фабрика подменяется и тянуть сеть незачем.
_TRANSPORT_IMPORTS: dict[Transport, str] = {
    Transport.FULL: "ConnectionTcpFull",
    Transport.ABRIDGED: "ConnectionTcpAbridged",
    Transport.INTERMEDIATE: "ConnectionTcpIntermediate",
    Transport.OBFUSCATED: "ConnectionTcpObfuscated",
    Transport.MTPROXY: "ConnectionTcpMTProxyRandomizedIntermediate",
}


def _connection_class(transport: Transport) -> object:
    from telethon import connection  # локальный импорт — не нужен в тестах

    return getattr(connection, _TRANSPORT_IMPORTS[transport])


def default_client_factory(
    settings: UserbotSettings, resolver: EndpointResolver, proxy: ProxyConfig | None = None
) -> ClientFactory:
    """Фабрика реального `TelegramClient` с полным набором параметров устойчивости.

    Каждое значение выбрано осознанно (spec 2026-07-31 §4.4):
    - `connection_retries=1` — ретраить один и тот же мёртвый адрес бессмысленно,
      ретраем занимается перебор точек, и он пробует ДРУГИЕ адреса. Дефолтные пять
      попыток по 10 секунд — это и есть те 54 секунды, на которых висел /health;
    - `flood_sleep_threshold=0` — иначе Telethon молча спит до минуты внутри запроса,
      и вызывающая сторона отваливается по таймауту вместо честного «флуд-лимит»;
    - `receive_updates=False` — апдейты сервису не нужны (обработчиков нет), но
      встроенный keep-alive ping при этом сохраняется;
    - device/app/lang заданы явно и стабильно: дефолты Telethon выводятся из версии
      ядра хоста и версии библиотеки, то есть меняются при каждом обновлении, и
      аккаунт выглядит «переехавшим на другое устройство».
    """

    def factory(session_str: str | None, endpoint: Endpoint) -> TelethonProtocol:
        from telethon import TelegramClient  # локальный импорт — не нужен в тестах

        session = PinnedStringSession(session_str, resolver)
        client = TelegramClient(
            session,
            settings.api_id,
            settings.api_hash.get_secret_value(),
            connection=_connection_class(endpoint.transport),
            proxy=proxy.value if (proxy is not None and endpoint.via_proxy) else None,
            use_ipv6=False,
            timeout=settings.connect_timeout,
            connection_retries=1,
            retry_delay=0,
            request_retries=3,
            auto_reconnect=True,
            flood_sleep_threshold=0,
            receive_updates=False,
            catch_up=False,
            device_model=settings.device_model,
            system_version=settings.system_version,
            app_version=settings.app_version,
            lang_code=settings.lang_code,
            system_lang_code=settings.lang_code,
        )
        return cast(TelethonProtocol, client)

    return factory


class AuthError(Exception):
    """Ошибка на шаге авторизации; `code` — короткий машинный код для API-ответа."""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


@dataclass(slots=True)
class _PendingAuth:
    """Незавершённая авторизация: клиент между шагами и срок его жизни."""

    client: TelethonProtocol
    deadline: float


class UserbotClient:
    """Реестр Telethon-клиентов по операторам, операции сервиса (spec §6).

    Одна попытка на операцию, без ретраев (spec §9). Клиент оператора создаётся
    лениво из сохранённой сессии; при auth-флоу — из пустой сессии, затем
    сохраняется под sender_id вызвавшего оператора.
    """

    def __init__(
        self,
        factory: ClientFactory,
        store: SessionStore,
        resolver: EndpointResolver | None = None,
        cache: EndpointCache | None = None,
        rounds: int = 3,
        budget: float = 45.0,
        states: StateRegistry | None = None,
        pending_ttl: float = 300.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._factory = factory
        self._store = store
        self._resolver = resolver or EndpointResolver()
        self._cache = cache
        self._rounds = max(1, rounds)
        self._budget = budget
        self.states = states if states is not None else StateRegistry()
        self._pending_ttl = pending_ttl
        self._clock = clock
        self._clients: dict[int, TelethonProtocol] = {}
        # Точка, через которую реально подключились: показываем её в диагностике.
        self._endpoints: dict[int, Endpoint] = {}
        # По локу на оператора: два параллельных запроса иначе поднимут двух клиентов
        # на одну сессию, а это ровно тот случай, когда Telegram отзывает ключ как
        # использованный с двух адресов сразу.
        self._locks: dict[int, asyncio.Lock] = {}
        # Незавершённые auth-флоу держат клиент между /auth/start и /auth/code;
        # словарь по sender_id — два оператора могут логиниться одновременно.
        self._pending: dict[int, _PendingAuth] = {}

    def _lock_for(self, sender_id: int) -> asyncio.Lock:
        lock = self._locks.get(sender_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[sender_id] = lock
        return lock

    async def _try_connect(
        self, session_str: str | None, endpoint: Endpoint
    ) -> TelethonProtocol | None:
        """Одна попытка подключения. `None` — точка не отозвалась.

        Клиент, у которого упал `connect()`, переиспользовать нельзя — внутри остаётся
        сломанный отправитель, поэтому на каждую точку собирается свежий.
        """
        client = self._factory(session_str, endpoint)
        try:
            await client.connect()
        except (ConnectionError, OSError, TimeoutError) as exc:
            logger.info("точка %s не отозвалась: %s", endpoint.label(), type(exc).__name__)
            with contextlib.suppress(Exception):
                await client.disconnect()
            return None
        return client

    async def _connect_with_fallback(
        self, session_str: str | None, candidates: list[Endpoint], *, sender_id: int | None = None
    ) -> tuple[TelethonProtocol, Endpoint]:
        """Перебрать точки до первой живой. Все мертвы → `UnreachableError`.

        Цепочка проходится НЕСКОЛЬКО раз. Фильтрация у провайдера вероятностная:
        на проде один и тот же адрес отвечает примерно в половине попыток, поэтому
        один проход даёт ложное «недоступно». Общий бюджет ограничивает время, чтобы
        вызывающая сторона получила ответ раньше своего таймаута.
        """
        deadline = time.monotonic() + self._budget
        attempts = 0
        for round_number in range(self._rounds):
            for endpoint in candidates:
                if time.monotonic() >= deadline:
                    raise UnreachableError(f"бюджет подключения исчерпан за {attempts} попыток")
                attempts += 1
                client = await self._try_connect(session_str, endpoint)
                if client is None:
                    continue
                logger.info(
                    "подключились через %s (попытка %s, круг %s)",
                    endpoint.label(),
                    attempts,
                    round_number + 1,
                )
                if self._cache is not None and sender_id is not None:
                    self._cache.remember(sender_id, endpoint)
                return client, endpoint
        raise UnreachableError(f"ни одна точка не отозвалась за {attempts} попыток")

    async def _get_client(self, sender_id: int) -> TelethonProtocol | None:
        """Подключённый авторизованный клиент оператора из его сессии; иначе None.

        `UnreachableError` пробрасывается: «сеть недоступна» и «сессия мертва» — разные
        состояния, и склеивать их в `None` значит давать оператору неверный совет.
        """
        async with self._lock_for(sender_id):
            cached = self._clients.get(sender_id)
            if cached is not None:
                if cached.is_connected():
                    return cached
                # Соединение отвалилось — держать такой клиент в кэше значит бить
                # об него все следующие запросы.
                logger.info("клиент оператора %s отключён — пересобираем", sender_id)
                await self._drop_client(sender_id)
            session_str = self._store.load(sender_id)
            if session_str is None:
                self.states.get(sender_id).state = SessionState.ABSENT
                return None
            candidates = self._candidates_for(session_str, sender_id)
            client, endpoint = await self._connect_with_fallback(
                session_str, candidates, sender_id=sender_id
            )
            if not await client.is_user_authorized():
                with contextlib.suppress(Exception):
                    await client.disconnect()
                self.states.mark_failed(
                    sender_id,
                    state=SessionState.EXPIRED,
                    error="session_expired",
                    now=self._clock(),
                )
                return None
            self._clients[sender_id] = client
            self._endpoints[sender_id] = endpoint
            return client

    async def _drop_client(self, sender_id: int) -> None:
        """Убрать клиента из кэша и закрыть соединение, не роняя вызывающего."""
        client = self._clients.pop(sender_id, None)
        self._endpoints.pop(sender_id, None)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def _note_failure(self, sender_id: int, exc: BaseException) -> str:
        """Записать сбой в состояние сессии и вернуть код ошибки §9."""
        error_class, code = classify(exc)
        state = state_for(error_class)
        if state is not None:
            self.states.mark_failed(sender_id, state=state, error=code, now=self._clock())
            # Мёртвый клиент в кэше — источник бесконечных одинаковых ошибок.
            await self._drop_client(sender_id)
        return code

    async def close(self) -> None:
        """Закрыть все соединения (вызывается при остановке сервиса)."""
        for sender_id in list(self._clients):
            await self._drop_client(sender_id)
        for pending in list(self._pending.values()):
            with contextlib.suppress(Exception):
                await pending.client.disconnect()
        self._pending.clear()

    def diagnostic_candidates(self) -> list[Endpoint]:
        """Точки для экрана диагностики: цепочки всех известных сессий + стартовые.

        Показываем ровно то, что реально перебирает сервис, иначе диагностика
        отвечала бы на другой вопрос, чем задаёт оператор.
        """
        chain: list[Endpoint] = []
        for sender_id in self.known_senders():
            session_str = self._store.load(sender_id)
            if session_str is None:
                continue
            for endpoint in self._candidates_for(session_str, sender_id):
                if endpoint not in chain:
                    chain.append(endpoint)
        for endpoint in self._resolver.auth_candidates():
            if endpoint not in chain:
                chain.append(endpoint)
        return chain

    def _candidates_for(self, session_str: str, sender_id: int) -> list[Endpoint]:
        """Цепочка точек для существующей сессии — строго в пределах её дата-центра."""
        known = session_endpoint(session_str, self._resolver)
        if known is None:
            # Строка сессии без адреса — брать нечего, идём как за новым логином.
            return self._resolver.auth_candidates()
        return self._resolver.candidates(known.dc_id, session_endpoint=known, sender_id=sender_id)

    def known_senders(self) -> list[int]:
        """Операторы, о которых мы вообще знаем: сохранённые сессии + активные клиенты."""
        return sorted(set(self._store.list_senders()) | set(self._clients))

    def health(self) -> dict[str, object]:
        """Состояние всех сессий ИЗ ПАМЯТИ — без единого сетевого вызова.

        Раньше этот метод подключался к Telegram по каждой сессии, из-за чего один
        опрос занимал десятки секунд: healthcheck контейнера не укладывался в таймаут,
        а поллер бота считал недоступным весь сервис. Состояние теперь обновляет
        фоновая проверка (`userbot/keepalive.py`), а читатели только смотрят.
        """
        for sender_id in self.known_senders():
            self.states.get(sender_id)
        return {"sessions": [info.as_dict() for info in self.states.snapshot()]}

    def health_for(self, sender_id: int) -> dict[str, object]:
        """Состояние одной сессии из памяти."""
        if sender_id not in self.known_senders():
            return SessionInfo(sender_id=sender_id, state=SessionState.ABSENT).as_dict()
        return self.states.get(sender_id).as_dict()

    async def probe(self, sender_id: int) -> dict[str, object]:
        """Форсированная проверка сессии по сети: обновляет состояние и возвращает его.

        Это единственный путь, который ходит в сеть по требованию, — им пользуются
        фоновая проверка и кнопка «проверить сейчас».
        """
        if not self._store.exists(sender_id) and sender_id not in self._clients:
            self.states.get(sender_id).state = SessionState.ABSENT
            return self.health_for(sender_id)
        try:
            client = await self._get_client(sender_id)
        except UnreachableError as exc:
            await self._note_failure(sender_id, exc)
            return self.health_for(sender_id)
        if client is None:
            return self.health_for(sender_id)
        try:
            me = await client.get_me()
        except Exception as exc:  # noqa: BLE001 — классификация решает, что это было
            await self._note_failure(sender_id, exc)
            return self.health_for(sender_id)
        endpoint = self._endpoints.get(sender_id)
        self.states.mark_ok(
            sender_id,
            phone=getattr(me, "phone", None),
            endpoint=endpoint.label() if endpoint is not None else None,
            now=self._clock(),
        )
        return self.health_for(sender_id)

    async def auth_start(self, sender_id: int, phone: str) -> str:
        """Шаг 1: запросить код на телефон, вернуть `phone_code_hash`.

        Сессия пустая, ключа авторизации ещё нет — поэтому здесь можно перебирать и
        сами дата-центры, а не только адреса. Домашний дата-центр номера Telegram
        назначит сам: ответит `PhoneMigrateError`, а Telethon переедет, взяв адрес
        через `PinnedStringSession.set_dc` — то есть уже исправленный.
        """
        await self._prune_pending()
        client, endpoint = await self._connect_with_fallback(None, self._resolver.auth_candidates())
        self._pending[sender_id] = _PendingAuth(
            client=client, deadline=self._clock() + self._pending_ttl
        )
        try:
            sent = await client.send_code_request(phone)
        except (ConnectionError, OSError, TimeoutError) as exc:
            # Дата-центр отвалился уже после установки соединения (например, на
            # миграции). Клиент не оставляем висеть.
            await self._drop_pending(sender_id)
            raise UnreachableError(f"обрыв на {endpoint.label()}: {type(exc).__name__}") from exc
        return str(sent.phone_code_hash)  # type: ignore[attr-defined]

    async def _prune_pending(self) -> None:
        """Выбросить протухшие незавершённые авторизации.

        Оператор мог начать привязку и не закончить: без TTL такой клиент держал бы
        соединение до перезапуска сервиса.
        """
        now = self._clock()
        stale = [key for key, pending in self._pending.items() if pending.deadline <= now]
        for sender_id in stale:
            logger.info("незавершённая авторизация оператора %s протухла", sender_id)
            await self._drop_pending(sender_id)

    async def _drop_pending(self, sender_id: int) -> None:
        """Убрать незавершённую авторизацию и закрыть её клиент."""
        pending = self._pending.pop(sender_id, None)
        if pending is not None:
            with contextlib.suppress(Exception):
                await pending.client.disconnect()

    async def auth_code(self, sender_id: int, phone: str, code: str, phone_code_hash: str) -> bool:
        """Шаг 2: ввод кода. Возвращает `needs_password` (True при включённой 2FA)."""
        client = self._require_pending(sender_id)
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        except errors.SessionPasswordNeededError:
            # Флоу продолжается: клиент нужен для шага с паролем, не закрываем.
            return True
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError) as exc:
            await self._drop_pending(sender_id)
            raise AuthError("phone_code_invalid") from exc
        self._finalize(sender_id, client)
        return False

    async def auth_password(self, sender_id: int, password: str) -> None:
        """Шаг 3 (2FA): ввод облачного пароля, завершение логина."""
        client = self._require_pending(sender_id)
        try:
            await client.sign_in(password=password)
        except errors.PasswordHashInvalidError as exc:
            await self._drop_pending(sender_id)
            raise AuthError("password_invalid") from exc
        self._finalize(sender_id, client)

    async def send(self, sender_id: int, username: str, text: str) -> tuple[str | None, str | None]:
        """Отправить сообщение от имени оператора → `(error, display_name)`.

        `error=None` — успех; `display_name` — имя получателя из Telegram (или None,
        если не заполнено). Нет сессии вовсе → `sender_not_authorized` (оператор ещё
        не проходил /link_userbot); сессия есть, но умерла → `session_expired`.
        """
        try:
            client = await self._get_client(sender_id)
        except UnreachableError as exc:
            # Сессия может быть жива — до Telegram не дошли. Не выдаём это за
            # «разлогинен»: иначе оператор зря пойдёт перепривязывать юзербота.
            return (await self._note_failure(sender_id, exc), None)
        if client is None:
            if self._store.exists(sender_id):
                return ("session_expired", None)
            return ("sender_not_authorized", None)
        try:
            # Резолвим сущность (для имени), затем отправляем — Telethon кеширует
            # entity, повторной сетевой операции по username не будет.
            entity = await client.get_entity(username)
            await client.send_message(username, text)
        except Exception as exc:  # noqa: BLE001 — любой сбой → код §9, наружу не бросаем
            # Состояние сессии обновляем здесь же: иначе мёртвый клиент остался бы
            # в кэше и следующие отправки бились бы об него.
            return (await self._note_failure(sender_id, exc), None)
        return (None, _display_name(entity))

    def _require_pending(self, sender_id: int) -> TelethonProtocol:
        pending = self._pending.get(sender_id)
        if pending is None:
            raise AuthError("no_pending_auth", "Сначала вызовите /auth/start")
        return pending.client

    def _finalize(self, sender_id: int, client: TelethonProtocol) -> None:
        """Сохранить сессию оператора и сделать его клиент активным."""
        session_str = client.session.save()
        self._store.save(sender_id, session_str)
        self._clients[sender_id] = client
        self._pending.pop(sender_id, None)
