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

from typing import Protocol, cast

from telethon import errors
from telethon.sessions import StringSession

from userbot.errors import map_send_error
from userbot.session import SessionStore


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
    """Фабрика клиента: принимает строку сессии (или None) → клиент Telethon."""

    def __call__(self, session_str: str | None) -> TelethonProtocol: ...


def default_client_factory(api_id: int, api_hash: str) -> ClientFactory:
    """Фабрика по умолчанию — реальный TelegramClient на StringSession."""

    def factory(session_str: str | None) -> TelethonProtocol:
        from telethon import TelegramClient  # локальный импорт — не нужен в тестах

        client = TelegramClient(StringSession(session_str), api_id, api_hash)
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

    def __init__(self, factory: ClientFactory, store: SessionStore) -> None:
        self._factory = factory
        self._store = store
        self._clients: dict[int, TelethonProtocol] = {}
        # Незавершённые auth-флоу держат клиент между /auth/start и /auth/code;
        # словарь по sender_id — два оператора могут логиниться одновременно.
        self._pending: dict[int, TelethonProtocol] = {}

    async def _get_client(self, sender_id: int) -> TelethonProtocol | None:
        """Подключённый авторизованный клиент оператора из его сессии; иначе None."""
        cached = self._clients.get(sender_id)
        if cached is not None:
            return cached
        session_str = self._store.load(sender_id)
        if session_str is None:
            return None
        client = self._factory(session_str)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return None
        self._clients[sender_id] = client
        return client

    async def health(self) -> dict[str, object]:
        """`{sessions: [{sender_id, authorized, phone?}, ...]}` по всем операторам."""
        sender_ids = sorted(set(self._store.list_senders()) | set(self._clients))
        sessions = [await self.health_for(sender_id) for sender_id in sender_ids]
        return {"sessions": sessions}

    async def health_for(self, sender_id: int) -> dict[str, object]:
        """`{sender_id, authorized, phone?}` — состояние сессии одного оператора."""
        client = await self._get_client(sender_id)
        if client is None:
            return {"sender_id": sender_id, "authorized": False}
        me = await client.get_me()
        phone = getattr(me, "phone", None)
        return {"sender_id": sender_id, "authorized": True, "phone": phone}

    async def auth_start(self, sender_id: int, phone: str) -> str:
        """Шаг 1: запросить код на телефон, вернуть `phone_code_hash`."""
        client = self._factory(None)
        await client.connect()
        self._pending[sender_id] = client
        sent = await client.send_code_request(phone)
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
        client = await self._get_client(sender_id)
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
