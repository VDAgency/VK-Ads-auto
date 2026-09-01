"""Отзыв доступа: граница сессий и отметка выпуска в токенах.

Ключевое свойство, ради которого сделано именно так: пока `sessions_valid_from`
пуст, проверка выпуска НЕ применяется — выкатка не разлогинивает никого.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from services.admin_auth import (
    generate_admin_link,
    generate_admin_session,
    verify_admin_link,
    verify_admin_session,
)
from services.auth_magiclink import generate_token, verify_token
from services.session_token import generate_session, verify_session

_SECRET = "test-secret-not-a-real-key"


def _legacy_session_or_admin_token(subject_id: int, purpose: str) -> str:
    """Собрать токен старого формата (до появления `issued_at`) вручную: 4 части,
    покрывает и `session_token` (`purpose="sess"`), и `admin_auth`
    (`purpose="admsess"`/`"admlink"`) — обе используют один и тот же payload-формат
    `purpose:subject_id:expires`.
    """
    import base64
    import hashlib
    import hmac

    expires = int(time.time()) + 3600
    payload = f"{purpose}:{subject_id}:{expires}"
    signature = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()


def _legacy_magiclink_token(client_id: int) -> str:
    """Токен magic-ссылки старого формата (до появления `issued_at`): 2 поля, без
    метки purpose — именно так выглядели ссылки, уже разосланные клиентам.
    """
    import base64
    import hashlib
    import hmac

    expires = int(time.time()) + 3600
    payload = f"{client_id}:{expires}"
    signature = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()


def test_session_without_boundary_is_accepted() -> None:
    token = generate_admin_session(777, _SECRET)
    assert verify_admin_session(token, _SECRET) == 777
    assert verify_admin_session(token, _SECRET, valid_from=None) == 777


def test_session_issued_before_boundary_is_rejected() -> None:
    token = generate_admin_session(777, _SECRET)
    boundary = datetime.now(UTC) + timedelta(seconds=5)
    assert verify_admin_session(token, _SECRET, valid_from=boundary) is None


def test_session_issued_after_boundary_is_accepted() -> None:
    boundary = datetime.now(UTC) - timedelta(seconds=5)
    token = generate_admin_session(777, _SECRET)
    assert verify_admin_session(token, _SECRET, valid_from=boundary) == 777


def test_legacy_token_passes_without_boundary_and_fails_with_one() -> None:
    """Токен старого формата (без отметки выпуска).

    Без границы — принимается: иначе выкатка разлогинила бы всех разом.
    С границей — отвергается: доказать, что он выпущен после отзыва, нечем.
    """
    import base64
    import hashlib
    import hmac

    expires = int(time.time()) + 3600
    payload = f"admsess:777:{expires}"
    signature = hmac.new(_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    legacy = base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()

    assert verify_admin_session(legacy, _SECRET) == 777
    assert verify_admin_session(legacy, _SECRET, valid_from=datetime.now(UTC)) is None


def test_naive_boundary_is_treated_as_utc() -> None:
    """SQLite в тестах отдаёт datetime без зоны — сравнение не должно падать."""
    token = generate_admin_session(777, _SECRET)
    naive_future = datetime.now(UTC).replace(tzinfo=None) + timedelta(seconds=5)
    assert verify_admin_session(token, _SECRET, valid_from=naive_future) is None


def test_cabinet_session_and_magiclink_honour_boundary() -> None:
    boundary = datetime.now(UTC) + timedelta(seconds=5)
    assert verify_session(generate_session(5, _SECRET), _SECRET, valid_from=boundary) is None
    assert verify_token(generate_token(5, _SECRET), _SECRET, valid_from=boundary) is None


def test_purpose_separation_survives_the_new_field() -> None:
    """Метка назначения по-прежнему не даёт подменить один токен другим."""
    assert verify_session(generate_admin_session(1, _SECRET), _SECRET) is None
    assert verify_admin_session(generate_session(1, _SECRET), _SECRET) is None


def test_no_cross_type_substitution_current_format() -> None:
    """Ни один из четырёх видов токена не проходит проверку другого вида (все 6
    сочетаний, обе стороны) — расширение `test_purpose_separation_survives_the_new_field`,
    которое покрывало только пару admin-сессия/сессия кабинета. Находка ревью
    2026-09-01: то, что длины payload у разных модулей НЕ пересекаются — неверное
    утверждение (session/admin допускают 4-5 частей, magic-ссылка — 3-4, общая
    точка — 4), поэтому здесь отдельно проверяем и magic-ссылку тоже.
    """
    admin_session = generate_admin_session(1, _SECRET)
    admin_link = generate_admin_link(1, _SECRET)
    cabinet_session = generate_session(1, _SECRET)
    magiclink = generate_token(1, _SECRET)

    # admin-сессия <-> admin-ссылка
    assert verify_admin_link(admin_session, _SECRET) is None
    assert verify_admin_session(admin_link, _SECRET) is None
    # admin-сессия <-> сессия кабинета
    assert verify_session(admin_session, _SECRET) is None
    assert verify_admin_session(cabinet_session, _SECRET) is None
    # admin-сессия <-> magic-ссылка
    assert verify_token(admin_session, _SECRET) is None
    assert verify_admin_session(magiclink, _SECRET) is None
    # admin-ссылка <-> сессия кабинета
    assert verify_session(admin_link, _SECRET) is None
    assert verify_admin_link(cabinet_session, _SECRET) is None
    # admin-ссылка <-> magic-ссылка
    assert verify_token(admin_link, _SECRET) is None
    assert verify_admin_link(magiclink, _SECRET) is None
    # сессия кабинета <-> magic-ссылка
    assert verify_token(cabinet_session, _SECRET) is None
    assert verify_session(magiclink, _SECRET) is None


def test_legacy_session_token_does_not_pass_as_magiclink() -> None:
    """Самый опасный случай из ревью 2026-09-01: легаси-токен сессии кабинета
    (`sess:client_id:expires:signature`, 4 части) имеет ТОТ ЖЕ секрет, что и
    magic-ссылка, а подписанная строка совпадает буквально — HMAC-проверку в
    `verify_token` он проходит. Раньше отклонялся только случайно (ветка «истёк»:
    на месте `expires` оказывался маленький `client_id`). Явный барьер в
    `auth_magiclink.verify_token` (первое поле обязано быть целым числом) отвергает
    его раньше и по назначению, а не полагаясь на совпадение.
    """
    legacy_cabinet_session = _legacy_session_or_admin_token(5, "sess")
    assert verify_token(legacy_cabinet_session, _SECRET) is None


def test_legacy_admin_tokens_do_not_pass_as_magiclink() -> None:
    """Тот же класс подмены — легаси-токены admin-сессии и admin-ссылки."""
    legacy_admin_session = _legacy_session_or_admin_token(5, "admsess")
    legacy_admin_link = _legacy_session_or_admin_token(5, "admlink")
    assert verify_token(legacy_admin_session, _SECRET) is None
    assert verify_token(legacy_admin_link, _SECRET) is None


def test_legacy_magiclink_does_not_pass_as_session_or_admin() -> None:
    """Легаси magic-ссылка (3 части, ещё без `issued_at`) — обратное направление
    подмены. У неё нет метки purpose вообще, поэтому session/admin отсеивают её по
    длине (`len(parts) not in (4, 5)`), а не по значению первого поля.
    """
    legacy_magiclink = _legacy_magiclink_token(5)
    assert verify_session(legacy_magiclink, _SECRET) is None
    assert verify_admin_session(legacy_magiclink, _SECRET) is None
    assert verify_admin_link(legacy_magiclink, _SECRET) is None


def test_non_numeric_first_field_rejected_before_signature_check() -> None:
    """Барьер в `auth_magiclink.verify_token` — не просто «эквивалент существующей
    защиты чуть раньше», а именно ДО проверки подписи (так и просил ревьюер): токен
    с нечисловым первым полем не должен доходить до `hmac.compare_digest`/`_sign`
    вообще. Проверяем это не по возвращаемому значению (оно и без барьера было бы
    `None` — по совпадению, через ветку «истёк», см. предыдущий тест), а по факту
    вызова `_sign`: если барьер убрать, `_sign` вызывается — тест краснеет.
    """
    from unittest.mock import patch

    import services.auth_magiclink as magiclink_module

    legacy_cabinet_session = _legacy_session_or_admin_token(5, "sess")
    with patch.object(magiclink_module, "_sign", wraps=magiclink_module._sign) as sign_spy:
        assert verify_token(legacy_cabinet_session, _SECRET) is None
        sign_spy.assert_not_called()
