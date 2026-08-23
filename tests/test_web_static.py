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
    assert "Бриф вместо технического задания" in body


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


def test_cabinet_page_shell_has_header() -> None:
    """Оболочка кабинета отдаётся статикой.

    Кнопка выхода рендерится условно — только когда кабинет загружен, поэтому
    в статическом HTML её нет. Прежде она лежала в разметке всегда и
    проверялась по id. Выход проверяется прогоном по чек-листу спеки §7.
    """
    client = TestClient(create_app())
    body = client.get("/cabinet.html").text
    assert "cab-header" in body
    assert 'Ads<span class="cab-brand__dot">·</span>auto' in body


def test_cabinet_page_shell_served() -> None:
    """Оболочка кабинета отдаётся статикой.

    Экран установки пароля теперь рендерится условно — только когда ядро
    ответило `password_set: false` на magic-link. Прежде он лежал в разметке
    скрытым и проверялся строкой; сейчас его в статике нет, и это осознанно:
    незачем отдавать всем разметку экрана, который увидит один клиент из ста.
    Поведение первого входа проверяется прогоном по чек-листу спеки §7.
    """
    client = TestClient(create_app())
    resp = client.get("/cabinet.html")
    assert resp.status_code == 200
    body = resp.text
    assert "cab-brand" in body  # шапка кабинета
    assert "skip-link" in body  # ссылка «к содержимому»


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
    assert "Рекламные кабинеты" in body  # управление кабинетами доступно и в вебе
    assert "adm-gate" in body  # экран «вход только из бота»
    assert "adm-nav__btn" in body  # навигация разделов
    assert 'id="app"' in body


def test_admin_page_never_ships_a_token_field_value() -> None:
    """Токен кабинета вводится, но не выводится: в разметке его быть не должно.

    Проверка грубая по построению — именно поэтому и полезная: если кто-то
    добавит показ токена в списке, тест это заметит.
    """
    client = TestClient(create_app())
    body = client.get("/admin.html").text
    assert "access_token=" not in body
    assert "token_encrypted" not in body


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


def test_brief_inputs_are_styled_regardless_of_type_attribute() -> None:
    """Поля ввода нельзя стилизовать только через `[type=...]`.

    Поля возраста рендерятся без атрибута `type` (браузер считает их текстовыми
    по умолчанию), поэтому селектор вида `input[type="text"]` на них не
    срабатывает и поле остаётся без стилей. Такая поломка не роняет ни сборку,
    ни разметку — видна только глазами, поэтому проверяется здесь.
    """
    client = TestClient(create_app())
    for page in _BRIEF_PAGES.values():
        css = page_css(client, page)
        # Селектор по элементу с исключением переключателей (минификатор
        # выкидывает кавычки внутри атрибута, поэтому проверяем обе формы).
        # Поля стилизуются селектором по элементу, без опоры на атрибут type.
        assert ".bf input" in css
        assert "[type=text]" not in css and '[type="text"]' not in css
        # Диапазон возраста — собственная сетка, а не растянутые на всю строку поля.
        assert ".bf-age" in css


def _goal_tab_buttons(body: str) -> list[str]:
    """Все `<button role="tab">…</button>` целиком (открывающий тег + текст)."""
    return re.findall(r'<button[^>]*role="tab"[^>]*>.*?</button>', body, re.DOTALL)


def test_brief_forms_show_five_goal_tabs_with_senler_locked() -> None:
    """Обе формы брифа задают вопрос вкладками: сначала цель, потом площадка внутри неё.

    Раньше был один вопрос «Куда привлекаем подписчиков?» на все 15 площадок сразу —
    половина из них (лид-форма, сообщения) подписчиков не привлекает. Пять вкладок,
    порядок и подписи — требование §1 спеки 2026-08-23-brief-goal-tabs-design.md.
    Заблокирована ровно одна — «Заявка через Senler»: её нет в справочнике площадок
    вовсе, а не просто «непроверена».
    """
    client = TestClient(create_app())
    for page in _BRIEF_PAGES.values():
        body = client.get(page).text
        tabs = _goal_tab_buttons(body)
        assert len(tabs) == 5, f"{page}: ожидалось 5 вкладок цели, получено {len(tabs)}"

        labels = [
            "Подписчики",
            "Вовлечение в готовый объект",
            "Сообщения сообществу",
            "Заявки — лид-форма",
            "Заявка через Senler",
        ]
        for tab, label in zip(tabs, labels, strict=True):
            assert label in tab, f"{page}: вкладка {label!r} не найдена по порядку"

        locked = [tab for tab in tabs if "disabled" in tab]
        assert len(locked) == 1, f"{page}: заблокирована должна быть ровно одна вкладка"
        assert "Заявка через Senler" in locked[0]
        assert 'class="bf-choice__soon"' in locked[0], "заблокированная вкладка помечена «скоро»"


def _goal_panel_html(body: str, key: str) -> str:
    """Разметка одной панели вкладки: `<fieldset id="goal-panel-{key}" …>…</fieldset>`.

    Панели всех вкладок смонтированы одновременно (`BriefGoalSurface` прячет
    неактивные через `hidden`/`disabled` на `<fieldset>`, а не убирает из DOM) —
    поэтому по всему `body` искать площадки одной цели нельзя, только внутри её
    собственного `<fieldset>`.
    """
    match = re.search(rf'<fieldset id="goal-panel-{key}"[^>]*>(.*?)</fieldset>', body, re.DOTALL)
    assert match, f"панель вкладки {key!r} не найдена"
    return match.group(0)


def test_brief_forms_default_tab_shows_subscription_surfaces() -> None:
    """Вкладка по умолчанию (Подписчики) отрисована на сервере со всеми площадками,
    видимой (без атрибута `hidden` на своём `<fieldset>`).

    Остальные вкладки тоже присутствуют в статике (панели вкладок смонтированы
    разом, см. `BriefGoalSurface`), но со своим `hidden`/`disabled` — их площадки
    здесь не считаются, чтобы тест не путал «есть в DOM» с «видно и доступно
    для отправки».
    """
    client = TestClient(create_app())
    for page in _BRIEF_PAGES.values():
        body = client.get(page).text
        panel = _goal_panel_html(body, "subscription")
        opening_tag = panel.split(">", 1)[0]
        assert "hidden" not in opening_tag, (
            f"{page}: вкладка «Подписчики» должна быть видимой по умолчанию"
        )
        radios = re.findall(r'<input type="radio"[^>]*name="target_type"[^>]*>', panel)
        assert len(radios) == 8, f"{page}: у цели «подписчики» должно быть 8 площадок"
        assert all("disabled" not in radio for radio in radios), (
            f"{page}: все восемь площадок подписки уже проверены боем"
        )

        engagement_panel = _goal_panel_html(body, "engagement")
        assert "hidden" in engagement_panel.split(">", 1)[0], (
            f"{page}: неактивная вкладка «Вовлечение» должна быть скрыта"
        )


def test_community_brief_goal_field_follows_active_tab() -> None:
    """Декоративное поле `goal` (services/brief_fields.py) заполняется вкладкой само.

    Сервер его не читает (цель выводится из target_type,
    services.goals.goal_for_target_type), но оператор видит в карточке брифа
    то же название цели, что видел клиент — а не пустое поле или отдельный,
    независимо выбираемый вопрос, как было раньше.
    """
    client = TestClient(create_app())
    body = client.get(_BRIEF_PAGES["community"]).text
    goal_input = re.search(r'<input[^>]*name="goal"[^>]*>', body)
    assert goal_input, "скрытое поле goal не найдено"
    assert 'value="Подписчики"' in goal_input.group(0)


def test_individual_brief_has_no_goal_field() -> None:
    """У физлица в канонической карте (services/brief_fields.py) поля `goal` нет —
    добавлять его нельзя: сдвинет нумерацию правок `номер.значение`."""
    client = TestClient(create_app())
    body = client.get(_BRIEF_PAGES["individual"]).text
    assert 'name="goal"' not in body


def test_object_url_copy_matches_default_subscription_tab() -> None:
    """Подсказка поля «ссылка на объект» — своя для цели (требование §4 спеки).

    На сервере отрисована вкладка по умолчанию (Подписчики) — её текст и
    проверяется; тексты остальных целей закреплены тестом на сверку каталогов
    (tests/test_brief_goal_tabs.py), не сборкой статики.
    """
    client = TestClient(create_app())
    for page in _BRIEF_PAGES.values():
        body = client.get(page).text
        assert "Ссылка на сообщество или страницу, куда привлекаем подписчиков" in body


def test_goal_tabs_are_keyboard_and_screen_reader_operable() -> None:
    """Вкладки — доступны с клавиатуры (роль tab/tablist) и понятны без цвета.

    `aria-selected` — не декоративный атрибут: без role="tablist"/"tab" скринридер
    не объявит группу вкладок вообще. Активная вкладка также отличается не только
    цветом — стиль `.bf-goal-tab.is-active` меняет начертание (см. brief.css).
    """
    client = TestClient(create_app())
    for page in _BRIEF_PAGES.values():
        body = client.get(page).text
        assert 'role="tablist"' in body
        assert body.count('role="tab"') == 5
        assert 'aria-selected="true"' in body
        assert 'aria-selected="false"' in body
    css = page_css(client, _BRIEF_PAGES["community"])
    assert ".bf-goal-tab" in css
    assert ".bf-goal-tab.is-active" in css or ".bf-goal-tab.is-active," in css


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
