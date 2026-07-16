"""Auth-флоу UserbotClient: с 2FA и без, по операторам (Telethon замокан). Spec §6."""

import asyncio
from pathlib import Path

import pytest
from userbot.telethon_client import AuthError

from tests._userbot_fakes import SENDER, FakeTelethon, make_client


def test_flow_without_2fa_saves_session(tmp_path: Path) -> None:
    async def scenario() -> None:
        fake = FakeTelethon(needs_2fa=False)
        client, fake = make_client(fake, tmp_path=str(tmp_path))

        phone_code_hash = await client.auth_start(SENDER, "+79990001122")
        assert phone_code_hash == "hash-abc"
        assert fake.code_requests == ["+79990001122"]

        needs_password = await client.auth_code(SENDER, "+79990001122", "12345", phone_code_hash)
        assert needs_password is False
        # Сессия оператора сохранена на диск после успешного логина.
        assert (tmp_path / f"{SENDER}.session.enc").exists()

    asyncio.run(scenario())


def test_flow_with_2fa(tmp_path: Path) -> None:
    async def scenario() -> None:
        fake = FakeTelethon(needs_2fa=True)
        client, _ = make_client(fake, tmp_path=str(tmp_path))

        phone_code_hash = await client.auth_start(SENDER, "+79990001122")
        needs_password = await client.auth_code(SENDER, "+79990001122", "12345", phone_code_hash)
        assert needs_password is True
        # После кода сессия ещё не сохранена — ждём пароль.
        assert not (tmp_path / f"{SENDER}.session.enc").exists()

        await client.auth_password(SENDER, "cloud-pass")
        assert (tmp_path / f"{SENDER}.session.enc").exists()

    asyncio.run(scenario())


def test_concurrent_flows_of_two_senders_are_isolated(tmp_path: Path) -> None:
    """Два оператора логинятся одновременно — pending-флоу не мешают друг другу."""

    async def scenario() -> None:
        fake_a = FakeTelethon(needs_2fa=False)
        fake_b = FakeTelethon(needs_2fa=False)
        client, _ = make_client(tmp_path=str(tmp_path), pending_queue=[fake_a, fake_b])

        hash_a = await client.auth_start(111, "+79990001111")
        hash_b = await client.auth_start(222, "+79990002222")
        assert fake_a.code_requests == ["+79990001111"]
        assert fake_b.code_requests == ["+79990002222"]

        await client.auth_code(111, "+79990001111", "11111", hash_a)
        # Логин первого не затронул pending второго.
        assert (tmp_path / "111.session.enc").exists()
        assert not (tmp_path / "222.session.enc").exists()

        await client.auth_code(222, "+79990002222", "22222", hash_b)
        assert (tmp_path / "222.session.enc").exists()

    asyncio.run(scenario())


def test_code_before_start_raises(tmp_path: Path) -> None:
    async def scenario() -> None:
        client, _ = make_client(tmp_path=str(tmp_path))
        with pytest.raises(AuthError) as exc:
            await client.auth_code(SENDER, "+7", "1", "h")
        assert exc.value.code == "no_pending_auth"

    asyncio.run(scenario())


def test_code_for_other_sender_pending_raises(tmp_path: Path) -> None:
    """Pending-флоу одного оператора не подходит другому: у каждого свой."""

    async def scenario() -> None:
        client, _ = make_client(tmp_path=str(tmp_path))
        await client.auth_start(111, "+79990001111")
        with pytest.raises(AuthError) as exc:
            await client.auth_code(222, "+79990002222", "1", "h")
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

        await client.auth_start(SENDER, "+79990001122")
        with pytest.raises(AuthError) as exc:
            await client.auth_code(SENDER, "+79990001122", "00000", "hash-abc")
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

        await client.auth_start(SENDER, "+79990001122")
        await client.auth_code(SENDER, "+79990001122", "12345", "hash-abc")
        with pytest.raises(AuthError) as exc:
            await client.auth_password(SENDER, "wrong")
        assert exc.value.code == "password_invalid"

    asyncio.run(scenario())
