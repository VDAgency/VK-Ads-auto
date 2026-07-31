"""Фейки Telethon для тестов userbot: конфигурируемый клиент без сети.

`FakeTelethon` реализует узкий `TelethonProtocol` из `userbot.telethon_client`.
Поведение задаётся флагами: авторизован ли, нужна ли 2FA, какое исключение бросить
на send. `client.session` — заглушка для `StringSession.save(...)`.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from cryptography.fernet import Fernet
from userbot.endpoints import Endpoint, EndpointResolver
from userbot.session import SessionStore
from userbot.telethon_client import ClientFactory, TelethonProtocol, UserbotClient


class _FakeSession:
    """Заглушка сессии — `StringSession.save()` умеет её сериализовать."""

    def save(self) -> str:
        return "fake-string-session"


class FakeTelethon:
    """Настраиваемый фейковый Telethon-клиент (реализует TelethonProtocol)."""

    def __init__(
        self,
        *,
        authorized: bool = False,
        needs_2fa: bool = False,
        send_error: Exception | None = None,
        phone: str | None = "+79990001122",
        first_name: str | None = None,
        last_name: str | None = None,
        connect_errors: list[Exception | None] | None = None,
        sign_in_error: Exception | None = None,
    ) -> None:
        self.sign_in_error = sign_in_error
        self.session = _FakeSession()
        self._authorized = authorized
        self._needs_2fa = needs_2fa
        self._send_error = send_error
        self._phone = phone
        self._first_name = first_name
        self._last_name = last_name
        # Очередь исходов connect(): исключение = точка не отозвалась, None = успех.
        # Пустая очередь означает «подключаемся всегда» (поведение по умолчанию).
        self._connect_errors = list(connect_errors or [])
        self.connected = False
        self.connect_calls = 0
        self.sent_messages: list[tuple[str, str]] = []
        self.code_requests: list[str] = []

    async def connect(self) -> None:
        self.connect_calls += 1
        if self._connect_errors:
            error = self._connect_errors.pop(0)
            if error is not None:
                raise error
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    def is_connected(self) -> bool:
        return self.connected

    async def is_user_authorized(self) -> bool:
        return self._authorized

    async def send_code_request(self, phone: str) -> object:
        self.code_requests.append(phone)
        return type("SentCode", (), {"phone_code_hash": "hash-abc"})()

    async def sign_in(
        self,
        phone: str | None = None,
        code: str | int | None = None,
        *,
        password: str | None = None,
        phone_code_hash: str | None = None,
    ) -> object:
        from telethon import errors

        if self.sign_in_error is not None:
            raise self.sign_in_error
        # Ввод кода: при включённой 2FA — требуем пароль.
        if password is None:
            if self._needs_2fa:
                raise errors.SessionPasswordNeededError(request=None)
            self._authorized = True
            return object()
        # Ввод пароля 2FA — успех.
        self._authorized = True
        return object()

    async def sign_in_password(self, password: str) -> object:  # pragma: no cover
        self._authorized = True
        return object()

    async def send_message(self, entity: str, message: str) -> object:
        if self._send_error is not None:
            raise self._send_error
        self.sent_messages.append((entity, message))
        return object()

    async def get_entity(self, entity: str) -> object:
        return type(
            "User",
            (),
            {
                "first_name": self._first_name,
                "last_name": self._last_name,
                "username": entity.lstrip("@"),
            },
        )()

    async def get_me(self) -> object:
        return type("Me", (), {"phone": self._phone})()


# Sender_id оператора по умолчанию в тестах (Telegram ID условного оператора).
SENDER = 111


def make_client(
    fake: FakeTelethon | None = None,
    *,
    tmp_path: str = "",
    saved_for: tuple[int, ...] = (),
    fakes_by_sender: dict[int, FakeTelethon] | None = None,
    pending_queue: list[FakeTelethon] | None = None,
    resolver: EndpointResolver | None = None,
    record: list[Endpoint] | None = None,
    fakes_by_endpoint: dict[Endpoint, FakeTelethon] | None = None,
    rounds: int = 3,
    budget: float = 45.0,
    pending_ttl: float = 300.0,
    clock: Callable[[], float] | None = None,
) -> tuple[UserbotClient, FakeTelethon]:
    """Собрать `UserbotClient` с фейковым Telethon и реальным SessionStore в tmp.

    - `saved_for` — sender_id, для которых заранее сохранить сессию (юзербот
      «уже авторизован» для этих операторов).
    - `fakes_by_sender` — отдельный фейк на каждого sender_id (тесты изоляции);
      сессия пишется как `session-{sender_id}`, фабрика находит фейк по ней.
    - `pending_queue` — фейки для auth-флоу (пустая сессия) по порядку вызовов;
      нужны для конкурентных логинов двух операторов.
    - `record` — сюда фабрика пишет КАЖДУЮ попытку подключения: так тест видит
      точный порядок перебора точек, не открывая ни одного сокета.
    - `fakes_by_endpoint` — свой фейк на конкретную точку (тесты перебора: одни
      точки «мертвы», другая отвечает).
    """
    fake = fake or FakeTelethon()
    key = Fernet.generate_key().decode("ascii")
    store = SessionStore(key, tmp_path or ".")
    for sender_id in saved_for:
        store.save(sender_id, f"session-{sender_id}")

    def factory(session_str: str | None, endpoint: Endpoint) -> TelethonProtocol:
        if record is not None:
            record.append(endpoint)
        if fakes_by_endpoint and endpoint in fakes_by_endpoint:
            return fakes_by_endpoint[endpoint]
        if session_str is None and pending_queue:
            return pending_queue.pop(0)
        if fakes_by_sender and session_str and session_str.startswith("session-"):
            return fakes_by_sender[int(session_str.removeprefix("session-"))]
        return fake

    typed_factory: ClientFactory = factory
    client = UserbotClient(
        factory=typed_factory,
        store=store,
        resolver=resolver,
        rounds=rounds,
        budget=budget,
        pending_ttl=pending_ttl,
        clock=clock if clock is not None else time.time,
    )
    return client, fake
