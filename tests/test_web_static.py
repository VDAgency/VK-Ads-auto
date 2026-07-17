from core.app import create_app
from fastapi.testclient import TestClient


def test_landing_served() -> None:
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    assert "VK" in response.text


def test_landing_has_brand_and_hero_headline() -> None:
    client = TestClient(create_app())
    body = client.get("/").text
    # Бренд лендинга и заголовок героя.
    assert 'Ads<span class="lp-brand__dot">·</span>auto' in body
    assert "От брифа до запущенной кампании" in body


def test_landing_links_both_brief_forms() -> None:
    client = TestClient(create_app())
    body = client.get("/").text
    # Обе CTA-ссылки на формы брифа сохранены.
    assert 'href="/brief-individual.html"' in body
    assert 'href="/brief-community.html"' in body


def test_landing_links_landing_css() -> None:
    client = TestClient(create_app())
    body = client.get("/").text
    assert '<link rel="stylesheet" href="/landing.css" />' in body


def test_landing_css_served() -> None:
    client = TestClient(create_app())
    response = client.get("/landing.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]


def test_landing_respects_reduced_motion() -> None:
    client = TestClient(create_app())
    # Уважение системной настройки reduced-motion — и в HTML, и в стилях лендинга.
    assert "prefers-reduced-motion" in client.get("/").text
    assert "prefers-reduced-motion" in client.get("/landing.css").text


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


def test_cabinet_page_has_auth_screens() -> None:
    client = TestClient(create_app())
    body = client.get("/cabinet.html").text
    assert client.get("/cabinet.html").status_code == 200
    # Экран установки пароля (первый вход) и вход по email+паролю.
    assert "Задайте пароль для входа" in body
    assert "/api/v1/cabinet/set-password" in body
    assert "/api/v1/cabinet/login" in body
    assert "/api/v1/cabinet/request-link" in body  # «забыли пароль»
    assert "Запишите или запомните его" in body  # явная инструкция про пароль


def test_brief_forms_mark_email_and_phone_required() -> None:
    client = TestClient(create_app())
    for form in ("/brief-individual.html", "/brief-community.html"):
        body = client.get(form).text
        assert "Email *" in body
        assert "Телефон *" in body
        assert "required" in body
