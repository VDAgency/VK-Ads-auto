"""Фейки Telethon для тестов userbot: конфигурируемый клиент без сети.

`FakeTelethon` реализует узкий `TelethonProtocol` из `userbot.telethon_client`.
Поведение задаётся флагами: авторизован ли, нужна ли 2FA, какое исключение бросить
на send. `client.session` — заглушка для `StringSession.save(...)`.
"""

from __future__ import annotations

from cryptography.fernet import Fernet
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
    ) -> None:
        self.session = _FakeSession()
        self._authorized = authorized
        self._needs_2fa = needs_2fa
        self._send_error = send_error
        self._phone = phone
        self.connected = False
        self.sent_messages: list[tuple[str, str]] = []
        self.code_requests: list[str] = []

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

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

    async def get_me(self) -> object:
        return type("Me", (), {"phone": self._phone})()


def make_client(
    fake: FakeTelethon | None = None,
    *,
    tmp_path: str = "",
    saved_session: bool = False,
) -> tuple[UserbotClient, FakeTelethon]:
    """Собрать `UserbotClient` с фейковым Telethon и реальным SessionStore в tmp.

    `saved_session=True` заранее пишет сессию в store — как будто юзербот уже
    авторизован (для тестов send/health).
    """
    fake = fake or FakeTelethon()
    key = Fernet.generate_key().decode("ascii")
    path = (tmp_path or ".") + "/anastasia.session.enc"
    store = SessionStore(key, path)
    if saved_session:
        store.save("preexisting-session")

    def factory(session_str: str | None) -> TelethonProtocol:
        return fake

    typed_factory: ClientFactory = factory
    return UserbotClient(factory=typed_factory, store=store), fake
