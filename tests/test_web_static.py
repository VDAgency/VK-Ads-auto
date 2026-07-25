import re

from core.app import create_app
from fastapi.testclient import TestClient

# Стили собираются Next в хешированные чанки под /_next/, поэтому обращаться к
# ним по имени файла (как к прежним /styles.css и /landing.css) больше нельзя.
# Находим их через разметку самой страницы — проверяем то же самое, но не
# завязываемся на имя, которое меняется от сборки к сборке.
_STYLESHEET_RE = re.compile(r'<link rel="stylesheet" href="([^"]+)"')


def stylesheet_hrefs(body: str) -> list[str]:
    """Адреса всех таблиц стилей, подключённых страницей."""
    return _STYLESHEET_RE.findall(body)


def page_css(client: TestClient, path: str) -> str:
    """Весь CSS, который реально получает страница по указанному адресу."""
    body = client.get(path).text
    chunks = []
    for href in stylesheet_hrefs(body):
        resp = client.get(href)
        assert resp.status_code == 200, f"таблица стилей {href} не отдаётся"
        chunks.append(resp.text)
    return "\n".join(chunks)


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


def test_landing_css_served() -> None:
    client = TestClient(create_app())
    body = client.get("/").text
    hrefs = stylesheet_hrefs(body)
    assert hrefs, "лендинг обязан подключать хотя бы одну таблицу стилей"
    for href in hrefs:
        response = client.get(href)
        assert response.status_code == 200
        assert "text/css" in response.headers["content-type"]


def test_landing_respects_reduced_motion() -> None:
    client = TestClient(create_app())
    # Стили уважают системную настройку. JS-часть (scroll-reveal сразу показывает
    # блоки при reduce) переехала в бандл вместе с инлайновым скриптом лендинга,
    # поэтому по HTML она больше не проверяется — см. прогон поведения (спека §7).
    assert "prefers-reduced-motion" in page_css(client, "/")


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


# Примечание к двум тестам ниже. Раньше они проверяли адреса эндпоинтов и
# редирект прямо в теле страницы — это работало, пока скрипт кабинета был
# инлайновым. После переноса на Next логика живёт в хешированном JS-чанке, и
# строковый поиск по HTML её больше не видит. Здесь остаётся то, что реально
# присутствует в статике (разметка и тексты экранов), а связка с API и редирект
# неавторизованных проверяются прогоном поведения по чек-листу спеки §7.


def test_cabinet_page_has_logout() -> None:
    client = TestClient(create_app())
    body = client.get("/cabinet.html").text
    assert 'id="logout"' in body
    assert "Выйти" in body


def test_cabinet_page_has_setpassword_screen() -> None:
    client = TestClient(create_app())
    resp = client.get("/cabinet.html")
    assert resp.status_code == 200
    body = resp.text
    # Экран установки пароля (первый вход) с крупными полями + инструкция.
    assert "Задайте пароль для входа" in body
    assert "Запишите или запомните его" in body
    assert "form-field" in body  # полноширинные поля, а не grid .field
    assert 'id="setpw-btn"' in body


def test_landing_has_login_modal() -> None:
    client = TestClient(create_app())
    body = client.get("/").text
    assert 'id="login-modal"' in body
    assert 'aria-labelledby="lm-title"' in body  # корректная связка dialog↔заголовок
    assert 'role="dialog"' in body
    assert "data-open-login" in body  # ссылки открывают модалку
    assert "form-field" in body  # полноширинные поля ввода
    assert "data-login-mode" in body  # сегментный переключатель способов входа
    assert "data-toggle-password" in body  # показать/скрыть пароль
    assert 'id="lm-email"' in body  # поле входа по паролю
    assert 'id="lm-forgot-email"' in body  # поле входа по ссылке на почту
    # Вызовы /api/v1/cabinet/{login,request-link} теперь в JS-бандле, а не в
    # разметке: проверяются прогоном поведения по чек-листу спеки §7.


def test_landing_css_has_modal_styles() -> None:
    client = TestClient(create_app())
    css = page_css(client, "/")
    assert ".lp-modal" in css
    assert "backdrop-filter" in css  # стеклянное затемнение
    assert ".lp-seg" in css  # сегментный переключатель
    assert ".lp-eye" in css  # кнопка показа пароля


def test_admin_page_served_with_sections() -> None:
    client = TestClient(create_app())
    resp = client.get("/admin.html")
    assert resp.status_code == 200
    body = resp.text
    # Оболочка админки: экран «нужен вход» и операторская навигация.
    # Экраны внутри (карточка брифа с правками и загрузкой креатива) рендерятся
    # только после выбора брифа, поэтому в статике их нет — как и вызовов
    # /api/v1/admin/*, уехавших в бандл. Проверяются прогоном поведения (§7).
    assert "Вход только из бота" in body
    assert "/admin" in body  # подсказка «команда /admin в боте»
    assert "Отправить бриф" in body  # веб-отправка брифа клиенту
    assert "Клиенты" in body
    assert "Пришли брифы" in body
    assert "Ждём брифы" in body
    assert "Кампании" in body
    assert 'id="logout"' in body
    assert 'id="need-auth"' in body
    assert 'id="app"' in body


_BRIEF_PAGES = {
    "individual": "/brief-individual.html",
    "community": "/brief-community.html",
}


def test_brief_forms_mark_email_and_phone_required() -> None:
    client = TestClient(create_app())
    for form in _BRIEF_PAGES.values():
        body = client.get(form).text
        assert "E-mail" in body
        assert "Телефон / WhatsApp" in body
        # Звёздочка обязательности — отдельный span, скрытый от скринридера
        # (обязательность ему сообщает атрибут required на самом поле).
        assert 'class="bf-req" aria-hidden="true"> *</span>' in body
        assert "required" in body


def test_brief_forms_have_vk_ad_cabinet_id_field() -> None:
    client = TestClient(create_app())
    for form in _BRIEF_PAGES.values():
        body = client.get(form).text
        # Обязательное поле «ID кабинета VK Реклама» + ссылка на инструкцию.
        assert "ID кабинета VK Реклама" in body
        assert 'name="vk_ad_cabinet_id"' in body
        assert 'href="/instrukciya-vk-cabinet.html"' in body


def test_brief_forms_cover_every_canonical_field() -> None:
    """Форма обязана собирать ВСЕ поля канонической карты варианта.

    Расхождение формы и `services/brief_fields.py` — это молчаливая потеря
    данных: поле есть в карточке оператора и в нумерации правок `номер.значение`,
    но клиенту его никто не показал, поэтому оно всегда пустое.
    """
    from services.brief_fields import fields_for

    client = TestClient(create_app())
    for variant, page in _BRIEF_PAGES.items():
        body = client.get(page).text
        missing = [f.key for f in fields_for(variant) if f'name="{f.key}"' not in body]
        assert not missing, f"{variant}: в форме нет полей {missing}"


def test_community_brief_offers_goals_and_locks_unavailable_ones() -> None:
    """Недоступные цели показаны, но выбрать их нельзя.

    `disabled` здесь не украшение: браузер не включает такие поля в FormData,
    поэтому цель, которую мы ещё не умеем запускать, физически не уедет в ядро.
    """
    client = TestClient(create_app())
    body = client.get(_BRIEF_PAGES["community"]).text

    radios = re.findall(r'<input type="radio"[^>]*name="goal"[^>]*>', body)
    assert len(radios) >= 2, "цели должны быть показаны списком"

    available = [r for r in radios if "disabled" not in r]
    locked = [r for r in radios if "disabled" in r]
    # Запускаем пока только подписчиков — она и единственная доступная.
    assert len(available) == 1
    assert 'value="подписчики"' in available[0]
    assert locked, "остальные цели должны быть заблокированы"
    # Каждая заблокированная цель помечена «скоро» — клиент видит, что она
    # существует, но ещё не подключена.
    assert body.count('class="bf-choice__soon"') == len(locked)


def test_extensionless_path_serves_html_file() -> None:
    # Статический экспорт Next кладёт роут /instrukciya-vk-cabinet в файл
    # instrukciya-vk-cabinet.html. Разосланные ссылки ведут на путь с .html,
    # внутренняя навигация Next — на путь без него; оба обязаны отдавать одно.
    client = TestClient(create_app())
    with_ext = client.get("/instrukciya-vk-cabinet.html")
    without_ext = client.get("/instrukciya-vk-cabinet")
    assert without_ext.status_code == 200
    assert without_ext.text == with_ext.text


def test_unknown_extensionless_path_is_not_found() -> None:
    client = TestClient(create_app())
    assert client.get("/no-such-page").status_code == 404
