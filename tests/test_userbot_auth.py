"""Auth-флоу UserbotClient: с 2FA и без (Telethon замокан). Spec §6."""

import asyncio
from pathlib import Path

import pytest
from userbot.telethon_client import AuthError

from tests._userbot_fakes import FakeTelethon, make_client


def test_flow_without_2fa_saves_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        fake = FakeTelethon(needs_2fa=False)
        client, fake = make_client(fake, tmp_path=str(tmp_path))

        phone_code_hash = await client.auth_start("+79990001122")
        assert phone_code_hash == "hash-abc"
        assert fake.code_requests == ["+79990001122"]

        needs_password = await client.auth_code("+79990001122", "12345", phone_code_hash)
        assert needs_password is False
        # Сессия сохранена на диск после успешного логина.
        assert (tmp_path / "anastasia.session.enc").exists()

    asyncio.run(scenario())


def test_flow_with_2fa(tmp_path: Path) -> None:
    async def scenario() -> None:
        fake = FakeTelethon(needs_2fa=True)
        client, _ = make_client(fake, tmp_path=str(tmp_path))

        phone_code_hash = await client.auth_start("+79990001122")
        needs_password = await client.auth_code("+79990001122", "12345", phone_code_hash)
        assert needs_password is True
        # После кода сессия ещё не сохранена — ждём пароль.
        assert not (tmp_path / "anastasia.session.enc").exists()

        await client.auth_password("cloud-pass")
        assert (tmp_path / "anastasia.session.enc").exists()

    asyncio.run(scenario())


def test_code_before_start_raises(tmp_path: Path) -> None:
    async def scenario() -> None:
        client, _ = make_client(tmp_path=str(tmp_path))
        with pytest.raises(AuthError) as exc:
            await client.auth_code("+7", "1", "h")
        assert exc.value.code == "no_pending_auth"

    asyncio.run(scenario())


def test_invalid_code_raises_auth_error(tmp_path: Path) -> None:
    from telethon import errors

    async def scenario() -> None:
        fake = FakeTelethon()

        async def bad_sign_in(*args: object, **kwargs: object) -> object:
            raise errors.PhoneCodeInvalidError(request=None)

        fake.sign_in = bad_sign_in  # type: ignore[method-assign]
        client, _ = make_client(fake, tmp_path=str(tmp_path))

        await client.auth_start("+79990001122")
        with pytest.raises(AuthError) as exc:
            await client.auth_code("+79990001122", "00000", "hash-abc")
        assert exc.value.code == "phone_code_invalid"

    asyncio.run(scenario())


def test_wrong_2fa_password_raises(tmp_path: Path) -> None:
    from telethon import errors

    async def scenario() -> None:
        fake = FakeTelethon(needs_2fa=True)

        async def bad_sign_in(
            phone: object = None,
            code: object = None,
            *,
            password: object = None,
            phone_code_hash: object = None,
        ) -> object:
            if password is not None:
                raise errors.PasswordHashInvalidError(request=None)
            raise errors.SessionPasswordNeededError(request=None)

        fake.sign_in = bad_sign_in  # type: ignore[method-assign]
        client, _ = make_client(fake, tmp_path=str(tmp_path))

        await client.auth_start("+79990001122")
        await client.auth_code("+79990001122", "12345", "hash-abc")
        with pytest.raises(AuthError) as exc:
            await client.auth_password("wrong")
        assert exc.value.code == "password_invalid"

    asyncio.run(scenario())
