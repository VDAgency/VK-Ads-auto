"""Обёртка над Telethon-клиентом: auth-флоу, отправка, health (spec §6).

`TelegramClient` инкапсулирован за узким `TelethonProtocol` и создаётся через
инъектируемую фабрику — так тесты подменяют Telethon моком без сети. Строка сессии
шифруется на диске через `SessionStore`; после успешного логина сохраняется.

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
    async def get_me(self) -> object: ...


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
    """Держит подключённый Telethon-клиент и реализует операции сервиса.

    Одна попытка на операцию, без ретраев (spec §9). Клиент создаётся лениво из
    сохранённой сессии; при auth-флоу — из пустой сессии, затем сохраняется.
    """

    def __init__(self, factory: ClientFactory, store: SessionStore) -> None:
        self._factory = factory
        self._store = store
        self._client: TelethonProtocol | None = None
        # Незавершённый auth-флоу держит клиент между /auth/start и /auth/code.
        self._pending_client: TelethonProtocol | None = None

    async def _get_client(self) -> TelethonProtocol | None:
        """Подключённый авторизованный клиент из сохранённой сессии; иначе None."""
        if self._client is not None:
            return self._client
        session_str = self._store.load()
        if session_str is None:
            return None
        client = self._factory(session_str)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return None
        self._client = client
        return client

    async def health(self) -> dict[str, object]:
        """`{authorized, phone?}` — состояние авторизации (spec §6)."""
        client = await self._get_client()
        if client is None:
            return {"authorized": False}
        me = await client.get_me()
        phone = getattr(me, "phone", None)
        return {"authorized": True, "phone": phone}

    async def auth_start(self, phone: str) -> str:
        """Шаг 1: запросить код на телефон, вернуть `phone_code_hash`."""
        client = self._factory(None)
        await client.connect()
        self._pending_client = client
        sent = await client.send_code_request(phone)
        return str(sent.phone_code_hash)  # type: ignore[attr-defined]

    async def auth_code(self, phone: str, code: str, phone_code_hash: str) -> bool:
        """Шаг 2: ввод кода. Возвращает `needs_password` (True при включённой 2FA)."""
        client = self._require_pending()
        try:
            await client.sign_in(phone, code, phone_code_hash=phone_code_hash)
        except errors.SessionPasswordNeededError:
            return True
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError) as exc:
            raise AuthError("phone_code_invalid") from exc
        self._finalize(client)
        return False

    async def auth_password(self, password: str) -> None:
        """Шаг 3 (2FA): ввод облачного пароля, завершение логина."""
        client = self._require_pending()
        try:
            await client.sign_in(password=password)
        except errors.PasswordHashInvalidError as exc:
            raise AuthError("password_invalid") from exc
        self._finalize(client)

    async def send(self, username: str, text: str) -> str | None:
        """Отправить сообщение. `None` — успех; иначе код ошибки §9."""
        client = await self._get_client()
        if client is None:
            return "session_expired"
        try:
            await client.send_message(username, text)
        except Exception as exc:  # noqa: BLE001 — любой сбой → код §9, наружу не бросаем
            return map_send_error(exc)
        return None

    def _require_pending(self) -> TelethonProtocol:
        if self._pending_client is None:
            raise AuthError("no_pending_auth", "Сначала вызовите /auth/start")
        return self._pending_client

    def _finalize(self, client: TelethonProtocol) -> None:
        """Сохранить сессию и сделать клиент активным."""
        session_str = client.session.save()
        self._store.save(session_str)
        self._client = client
        self._pending_client = None
