from services.auth_magiclink import generate_token, verify_token

SECRET = "test-secret"


def test_roundtrip() -> None:
    token = generate_token(42, SECRET)
    assert verify_token(token, SECRET) == 42


def test_wrong_secret_rejected() -> None:
    token = generate_token(42, SECRET)
    assert verify_token(token, "other-secret") is None


def test_tampered_token_rejected() -> None:
    token = generate_token(42, SECRET) + "tampered"
    assert verify_token(token, SECRET) is None


def test_expired_token_rejected() -> None:
    token = generate_token(42, SECRET, ttl_seconds=-1)
    assert verify_token(token, SECRET) is None


def test_garbage_rejected() -> None:
    assert verify_token("not-a-real-token", SECRET) is None
