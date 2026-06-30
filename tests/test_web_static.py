from core.app import create_app
from fastapi.testclient import TestClient


def test_landing_served() -> None:
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert "VK" in response.text


def test_health_still_works_with_static_mount() -> None:
    client = TestClient(create_app())
    assert client.get("/health").json() == {"status": "ok"}


def test_api_ping_still_works_with_static_mount() -> None:
    client = TestClient(create_app())
    assert client.get("/api/v1/ping").json() == {"pong": True}


def test_individual_brief_form_served() -> None:
    client = TestClient(create_app())
    response = client.get("/brief-individual.html")
    assert response.status_code == 200
    assert 'data-variant="individual"' in response.text


def test_community_brief_form_served() -> None:
    client = TestClient(create_app())
    response = client.get("/brief-community.html")
    assert response.status_code == 200
    assert 'data-variant="community"' in response.text
