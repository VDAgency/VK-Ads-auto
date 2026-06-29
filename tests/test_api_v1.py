from core.app import create_app
from fastapi.testclient import TestClient


def test_v1_ping() -> None:
    client = TestClient(create_app())
    response = client.get("/api/v1/ping")
    assert response.status_code == 200
    assert response.json() == {"pong": True}
