"""API-слой userbot: контракт эндпоинтов через FastAPI TestClient. Spec §6, §9.

Клиент подменяется на настоящий UserbotClient с фейковым Telethon (без сети).
Все эндпоинты работают в разрезе операторов (`sender_id`).
"""

from pathlib import Path

from fastapi.testclient import TestClient
from telethon import errors
from userbot.main import create_app

from tests._userbot_fakes import SENDER, FakeTelethon, make_client


def _app_with(client_obj: object) -> TestClient:
    app = create_app()
    # Подменяем lifespan-инициализацию: кладём готовый клиент прямо в state.
    app.state.client = client_obj
    tc = TestClient(app)
    return tc


def test_live_endpoint_needs_nothing(tmp_path: Path) -> None:
    """Liveness обязан отвечать даже с мёртвой сетью — на него смотрит docker."""
    fake = FakeTelethon(authorized=True, connect_errors=[ConnectionError("dead")] * 50)
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
    with _app_with(client) as tc:
        resp = tc.get("/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert fake.connect_calls == 0


def test_health_endpoint_lists_sessions(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=True, phone="+79990001122")
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
    with _app_with(client) as tc:
        tc.post(f"/sessions/{SENDER}/probe")
        resp = tc.get("/health")
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert sessions[0]["sender_id"] == SENDER
    assert sessions[0]["state"] == "ready"
    assert sessions[0]["phone"] == "+79990001122"


def test_sessions_endpoint_does_not_touch_network(tmp_path: Path) -> None:
    """Регресс на инцидент: чтение состояния не должно подключаться к Telegram."""
    fake = FakeTelethon(authorized=True, connect_errors=[ConnectionError("dead")] * 50)
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
    with _app_with(client) as tc:
        resp = tc.get("/sessions")
    assert resp.status_code == 200
    assert fake.connect_calls == 0
    assert resp.json()["sessions"][0]["state"] == "unknown"


def test_probe_endpoint_updates_state(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=True, phone="+79990001122")
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
    with _app_with(client) as tc:
        probed = tc.post(f"/sessions/{SENDER}/probe")
        read_back = tc.get(f"/sessions/{SENDER}")
    assert probed.json()["state"] == "ready"
    assert read_back.json()["state"] == "ready"


def test_diagnostics_endpoint_returns_matrix(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path=str(tmp_path), saved_for=(SENDER,))
    with _app_with(client) as tc:
        resp = tc.get("/diagnostics/endpoints")
    assert resp.status_code == 200
    rows = resp.json()["endpoints"]
    assert rows, "матрица не должна быть пустой при известной сессии"
    assert {"label", "reachable", "via_proxy"} <= set(rows[0])


def test_health_endpoint_empty(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path=str(tmp_path))
    with _app_with(client) as tc:
        resp = tc.get("/health")
    assert resp.json() == {"sessions": []}


def test_health_endpoint_filter_by_sender(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=True, phone="+79990001122")
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
    with _app_with(client) as tc:
        tc.post(f"/sessions/{SENDER}/probe")
        resp = tc.get("/health", params={"sender_id": SENDER})
        missing = tc.get("/health", params={"sender_id": 999})
    assert resp.json()["state"] == "ready"
    assert resp.json()["phone"] == "+79990001122"
    assert missing.json()["state"] == "absent"


def test_send_endpoint_ok(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=True)
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
    with _app_with(client) as tc:
        resp = tc.post("/send", json={"sender_id": SENDER, "username": "@ivanov", "text": "бриф"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_send_endpoint_returns_display_name(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=True, first_name="Вячеслав")
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
    with _app_with(client) as tc:
        resp = tc.post("/send", json={"sender_id": SENDER, "username": "@cs", "text": "бриф"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "display_name": "Вячеслав"}


def test_send_endpoint_username_not_occupied(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=True, send_error=errors.UsernameNotOccupiedError(request=None))
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
    with _app_with(client) as tc:
        resp = tc.post("/send", json={"sender_id": SENDER, "username": "@nobody", "text": "t"})
    assert resp.status_code == 400
    assert resp.json() == {"ok": False, "error": "username_not_occupied"}


def test_send_endpoint_peer_flood_429(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=True, send_error=errors.PeerFloodError(request=None))
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
    with _app_with(client) as tc:
        resp = tc.post("/send", json={"sender_id": SENDER, "username": "@x", "text": "t"})
    assert resp.status_code == 429
    assert resp.json()["error"] == "peer_flood"


def test_send_endpoint_unlinked_sender_401(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path=str(tmp_path))
    with _app_with(client) as tc:
        resp = tc.post("/send", json={"sender_id": SENDER, "username": "@x", "text": "t"})
    assert resp.status_code == 401
    assert resp.json()["error"] == "sender_not_authorized"


def test_send_endpoint_dead_session_401(tmp_path: Path) -> None:
    fake = FakeTelethon(authorized=False)
    client, _ = make_client(fake, tmp_path=str(tmp_path), saved_for=(SENDER,))
    with _app_with(client) as tc:
        resp = tc.post("/send", json={"sender_id": SENDER, "username": "@x", "text": "t"})
    assert resp.status_code == 401
    assert resp.json()["error"] == "session_expired"


def test_auth_flow_endpoints_without_2fa(tmp_path: Path) -> None:
    fake = FakeTelethon(needs_2fa=False)
    client, _ = make_client(fake, tmp_path=str(tmp_path))
    with _app_with(client) as tc:
        start = tc.post("/auth/start", json={"sender_id": SENDER, "phone": "+79990001122"})
        assert start.status_code == 200
        hash_ = start.json()["phone_code_hash"]

        code = tc.post(
            "/auth/code",
            json={
                "sender_id": SENDER,
                "phone": "+79990001122",
                "code": "12345",
                "phone_code_hash": hash_,
            },
        )
    assert code.status_code == 200
    assert code.json() == {"ok": True, "needs_password": False}


def test_auth_code_endpoint_with_2fa_then_password(tmp_path: Path) -> None:
    fake = FakeTelethon(needs_2fa=True)
    client, _ = make_client(fake, tmp_path=str(tmp_path))
    with _app_with(client) as tc:
        start = tc.post("/auth/start", json={"sender_id": SENDER, "phone": "+79990001122"})
        hash_ = start.json()["phone_code_hash"]
        code = tc.post(
            "/auth/code",
            json={
                "sender_id": SENDER,
                "phone": "+79990001122",
                "code": "12345",
                "phone_code_hash": hash_,
            },
        )
        assert code.json() == {"ok": True, "needs_password": True}

        pwd = tc.post("/auth/password", json={"sender_id": SENDER, "password": "cloud-pass"})
    assert pwd.status_code == 200
    assert pwd.json() == {"ok": True}


def test_auth_code_invalid_returns_400(tmp_path: Path) -> None:
    fake = FakeTelethon()

    async def bad_sign_in(*args: object, **kwargs: object) -> object:
        raise errors.PhoneCodeInvalidError(request=None)

    fake.sign_in = bad_sign_in  # type: ignore[method-assign]
    client, _ = make_client(fake, tmp_path=str(tmp_path))
    with _app_with(client) as tc:
        tc.post("/auth/start", json={"sender_id": SENDER, "phone": "+79990001122"})
        resp = tc.post(
            "/auth/code",
            json={
                "sender_id": SENDER,
                "phone": "+79990001122",
                "code": "0",
                "phone_code_hash": "h",
            },
        )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "phone_code_invalid"
