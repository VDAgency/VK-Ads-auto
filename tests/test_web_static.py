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


def test_instruction_page_served() -> None:
    client = TestClient(create_app())
    response = client.get("/instrukciya-vk-cabinet.html")
    assert response.status_code == 200
    # Ключевые ориентиры инструкции по созданию кабинета VK Реклама.
    assert "ID кабинета" in response.text
    assert "Рекламодатель" in response.text
    assert "ads.vk.ru" in response.text


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


def test_landing_has_cabinet_login_link() -> None:
    client = TestClient(create_app())
    body = client.get("/").text
    assert 'href="/cabinet.html"' in body
    assert "Вход в кабинет" in body


def test_cabinet_page_has_logout() -> None:
    client = TestClient(create_app())
    body = client.get("/cabinet.html").text
    assert 'id="logout"' in body
    assert "/api/v1/cabinet/logout" in body


def test_cabinet_page_has_setpassword_and_redirect() -> None:
    client = TestClient(create_app())
    resp = client.get("/cabinet.html")
    assert resp.status_code == 200
    body = resp.text
    # Экран установки пароля (первый вход) с крупными полями + инструкция.
    assert "Задайте пароль для входа" in body
    assert "/api/v1/cabinet/set-password" in body
    assert "Запишите или запомните его" in body
    assert "form-field" in body  # полноширинные поля, а не grid .field
    # Вход — через модалку на главной: неавторизованных редиректим туда.
    assert "/?login=1" in body


def test_landing_has_login_modal() -> None:
    client = TestClient(create_app())
    body = client.get("/").text
    assert 'id="login-modal"' in body
    assert 'aria-labelledby="lm-title"' in body  # корректная связка dialog↔заголовок
    assert "data-open-login" in body  # ссылки открывают модалку
    assert "/api/v1/cabinet/login" in body
    assert "/api/v1/cabinet/request-link" in body  # вход по ссылке на почту
    assert "form-field" in body  # полноширинные поля ввода
    assert "data-login-mode" in body  # сегментный переключатель способов входа
    assert "data-toggle-password" in body  # показать/скрыть пароль


def test_landing_css_has_modal_styles() -> None:
    client = TestClient(create_app())
    css = client.get("/landing.css").text
    assert ".lp-modal" in css
    assert "backdrop-filter" in css  # стеклянное затемнение
    assert ".lp-seg" in css  # сегментный переключатель
    assert ".lp-eye" in css  # кнопка показа пароля


def test_admin_page_served_with_sections() -> None:
    client = TestClient(create_app())
    resp = client.get("/admin.html")
    assert resp.status_code == 200
    body = resp.text
    # Вход только из бота + вызовы админ-эндпоинтов + операторские действия.
    assert "Вход только из бота" in body
    assert "/api/v1/admin" in body  # база админ-API
    assert "/authenticate" in body
    assert "/overview" in body
    assert "Внести правки" in body
    assert "Загрузить креатив" in body
    assert "Отправить бриф" in body  # веб-отправка брифа клиенту
    assert "/invites" in body  # вызов POST /api/v1/admin/invites


def test_brief_forms_mark_email_and_phone_required() -> None:
    client = TestClient(create_app())
    for form in ("/brief-individual.html", "/brief-community.html"):
        body = client.get(form).text
        assert "Email *" in body
        assert "Телефон *" in body
        assert "required" in body


def test_brief_forms_have_vk_ad_cabinet_id_field() -> None:
    client = TestClient(create_app())
    for form in ("/brief-individual.html", "/brief-community.html"):
        body = client.get(form).text
        # Обязательное поле «ID кабинета VK Реклама» + ссылка на инструкцию.
        assert "ID кабинета VK Реклама *" in body
        assert 'name="vk_ad_cabinet_id"' in body
        assert 'href="/instrukciya-vk-cabinet.html"' in body
