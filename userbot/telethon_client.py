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

import contextlib
import logging
from typing import Protocol, cast

from telethon import errors

from userbot.config import UserbotSettings
from userbot.endpoint_cache import EndpointCache
from userbot.endpoints import Endpoint, EndpointResolver, Transport
from userbot.errors import map_send_error
from userbot.pinned_session import PinnedStringSession, session_endpoint
from userbot.proxy import ProxyConfig
from userbot.session import SessionStore

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
    ) -> None:
        self._factory = factory
        self._store = store
        self._resolver = resolver or EndpointResolver()
        self._cache = cache
        self._clients: dict[int, TelethonProtocol] = {}
        # Незавершённые auth-флоу держат клиент между /auth/start и /auth/code;
        # словарь по sender_id — два оператора могут логиниться одновременно.
        self._pending: dict[int, TelethonProtocol] = {}

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
        """Перебрать точки до первой живой. Все мертвы → `UnreachableError`."""
        for endpoint in candidates:
            client = await self._try_connect(session_str, endpoint)
            if client is None:
                continue
            logger.info("подключились через %s", endpoint.label())
            if self._cache is not None and sender_id is not None:
                self._cache.remember(sender_id, endpoint)
            return client, endpoint
        raise UnreachableError(f"ни одна из {len(candidates)} точек подключения не отозвалась")

    async def _get_client(self, sender_id: int) -> TelethonProtocol | None:
        """Подключённый авторизованный клиент оператора из его сессии; иначе None.

        `UnreachableError` пробрасывается: «сеть недоступна» и «сессия мертва» — разные
        состояния, и склеивать их в `None` значит давать оператору неверный совет.
        """
        cached = self._clients.get(sender_id)
        if cached is not None:
            return cached
        session_str = self._store.load(sender_id)
        if session_str is None:
            return None
        candidates = self._candidates_for(session_str, sender_id)
        client, _ = await self._connect_with_fallback(session_str, candidates, sender_id=sender_id)
        if not await client.is_user_authorized():
            with contextlib.suppress(Exception):
                await client.disconnect()
            return None
        self._clients[sender_id] = client
        return client

    def _candidates_for(self, session_str: str, sender_id: int) -> list[Endpoint]:
        """Цепочка точек для существующей сессии — строго в пределах её дата-центра."""
        known = session_endpoint(session_str, self._resolver)
        if known is None:
            # Строка сессии без адреса — брать нечего, идём как за новым логином.
            return self._resolver.auth_candidates()
        return self._resolver.candidates(known.dc_id, session_endpoint=known, sender_id=sender_id)

    async def health(self) -> dict[str, object]:
        """`{sessions: [{sender_id, authorized, phone?}, ...]}` по всем операторам."""
        sender_ids = sorted(set(self._store.list_senders()) | set(self._clients))
        sessions = [await self.health_for(sender_id) for sender_id in sender_ids]
        return {"sessions": sessions}

    async def health_for(self, sender_id: int) -> dict[str, object]:
        """`{sender_id, authorized, phone?}` — состояние сессии одного оператора.

        Недоступность Telegram отдаётся отдельным полем `error`, а не молчаливым
        `authorized: false`: это разные ситуации и лечатся они по-разному.
        """
        try:
            client = await self._get_client(sender_id)
        except UnreachableError:
            return {"sender_id": sender_id, "authorized": False, "error": "unreachable"}
        if client is None:
            return {"sender_id": sender_id, "authorized": False}
        try:
            me = await client.get_me()
        except (ConnectionError, OSError, TimeoutError):
            return {"sender_id": sender_id, "authorized": False, "error": "unreachable"}
        phone = getattr(me, "phone", None)
        return {"sender_id": sender_id, "authorized": True, "phone": phone}

    async def auth_start(self, sender_id: int, phone: str) -> str:
        """Шаг 1: запросить код на телефон, вернуть `phone_code_hash`.

        Сессия пустая, ключа авторизации ещё нет — поэтому здесь можно перебирать и
        сами дата-центры, а не только адреса. Домашний дата-центр номера Telegram
        назначит сам: ответит `PhoneMigrateError`, а Telethon переедет, взяв адрес
        через `PinnedStringSession.set_dc` — то есть уже исправленный.
        """
        client, endpoint = await self._connect_with_fallback(None, self._resolver.auth_candidates())
        self._pending[sender_id] = client
        try:
            sent = await client.send_code_request(phone)
        except (ConnectionError, OSError, TimeoutError) as exc:
            # Дата-центр отвалился уже после установки соединения (например, на
            # миграции). Клиент не оставляем висеть.
            self._pending.pop(sender_id, None)
            with contextlib.suppress(Exception):
                await client.disconnect()
            raise UnreachableError(f"обрыв на {endpoint.label()}: {type(exc).__name__}") from exc
        return str(sent.phone_code_hash)  # type: ignore[attr-defined]

    async def auth_code(self, sender_id: int, phone: str, code: str, phone_code_hash: str) -> bool:
        """Шаг 2: ввод кода. Возвращает `needs_password` (True при включённой 2FA)."""
        client = self._require_pending(sender_id)
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        except errors.SessionPasswordNeededError:
            return True
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError) as exc:
            raise AuthError("phone_code_invalid") from exc
        self._finalize(sender_id, client)
        return False

    async def auth_password(self, sender_id: int, password: str) -> None:
        """Шаг 3 (2FA): ввод облачного пароля, завершение логина."""
        client = self._require_pending(sender_id)
        try:
            await client.sign_in(password=password)
        except errors.PasswordHashInvalidError as exc:
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
        except UnreachableError:
            # Сессия может быть жива — до Telegram не дошли. Не выдаём это за
            # «разлогинен»: иначе оператор зря пойдёт перепривязывать юзербота.
            return ("userbot_unreachable", None)
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
            return (map_send_error(exc), None)
        return (None, _display_name(entity))

    def _require_pending(self, sender_id: int) -> TelethonProtocol:
        client = self._pending.get(sender_id)
        if client is None:
            raise AuthError("no_pending_auth", "Сначала вызовите /auth/start")
        return client

    def _finalize(self, sender_id: int, client: TelethonProtocol) -> None:
        """Сохранить сессию оператора и сделать его клиент активным."""
        session_str = client.session.save()
        self._store.save(sender_id, session_str)
        self._clients[sender_id] = client
        self._pending.pop(sender_id, None)
