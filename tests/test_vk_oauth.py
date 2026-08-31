"""OAuth2-клиент VK Ads (агентский доступ): выпуск/обновление/удаление токена.

Тело запроса — `application/x-www-form-urlencoded` (документация VK, не JSON,
как у остальных `/api/v2/*.json`). Формы ответов и параметров взяты из живой
документации VK (`ads.vk.com/doc/api/info/Авторизация в API`, context7,
2026-08-31): grant `agency_client_credentials` принимает `agency_client_id`
ИЛИ `agency_client_name`; `expires_in` в ответе — строка секунд.
"""

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import Any, TypeVar

import httpx
import pytest
import respx
from integrations.vk_oauth import (
    TOKEN_DELETE_URL,
    TOKEN_URL,
    VkOAuthInvalidCredentials,
    VkOAuthNotConfigured,
    VkOAuthRejected,
    VkOAuthToken,
    VkOAuthUnavailable,
    delete_agency_tokens,
    refresh_agency_token,
    request_agency_client_token,
    request_own_account_token,
)

TOKEN_RESPONSE = {
    "access_token": "issued-access-token",
    "token_type": "bearer",
    "scope": "read_all",
    "expires_in": "86400",
    "refresh_token": "issued-refresh-token",
}

_T = TypeVar("_T")


def _run(coro: Coroutine[Any, Any, _T]) -> _T:
    return asyncio.run(coro)


# --- Выпуск токена клиенту агентства ---------------------------------------


@respx.mock
def test_issue_token_by_agency_client_id() -> None:
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
    token = _run(request_agency_client_token("cid", "csecret", agency_client_id="12345"))
    assert isinstance(token, VkOAuthToken)
    assert token.access_token.get_secret_value() == "issued-access-token"
    assert token.refresh_token.get_secret_value() == "issued-refresh-token"
    assert token.token_type == "bearer"
    sent = route.calls.last.request.content.decode()
    assert "grant_type=agency_client_credentials" in sent
    assert "agency_client_id=12345" in sent
    assert "agency_client_name" not in sent
    assert "client_id=cid" in sent
    assert "client_secret=csecret" in sent


@respx.mock
def test_issue_token_by_agency_client_name() -> None:
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
    token = _run(
        request_agency_client_token("cid", "csecret", agency_client_name="client_username")
    )
    assert token.access_token.get_secret_value() == "issued-access-token"
    sent = route.calls.last.request.content.decode()
    assert "agency_client_name=client_username" in sent
    assert "agency_client_id" not in sent


@respx.mock
def test_issue_token_sends_form_encoded_body() -> None:
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
    _run(request_agency_client_token("cid", "csecret", agency_client_id="1"))
    request = route.calls.last.request
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"


@respx.mock
def test_expires_at_computed_from_response_time() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
    before = datetime.now(UTC)
    token = _run(request_agency_client_token("cid", "csecret", agency_client_id="1"))
    after = datetime.now(UTC)
    delta_low = (token.expires_at - before).total_seconds()
    delta_high = (token.expires_at - after).total_seconds()
    # 86400 секунд (сутки) от момента ответа, с запасом на время выполнения теста.
    assert 86390 <= delta_low <= 86401
    assert 86390 <= delta_high <= 86401


def test_neither_agency_ref_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        _run(request_agency_client_token("cid", "csecret"))


def test_both_agency_refs_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        _run(
            request_agency_client_token(
                "cid", "csecret", agency_client_id="1", agency_client_name="u"
            )
        )


# --- Выпуск токена собственного аккаунта (agency_cabinets, правка после ревью) --
#
# `grant_type=client_credentials` — «Client Credentials Grant» из документации
# VK, доступ к данным СОБСТВЕННОГО аккаунта. Именно им агентство удостоверяет
# себя перед `/agency/clients.json`, вместо долгоживущего токена из окружения
# (см. services/agency_cabinets.py). Ни ссылки на клиента, ни выбора между
# id/username здесь нет — только пара ключей приложения.


@respx.mock
def test_own_account_token_sends_client_credentials_grant() -> None:
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
    token = _run(request_own_account_token("cid", "csecret"))
    assert isinstance(token, VkOAuthToken)
    assert token.access_token.get_secret_value() == "issued-access-token"
    assert token.refresh_token.get_secret_value() == "issued-refresh-token"
    sent = route.calls.last.request.content.decode()
    assert "grant_type=client_credentials" in sent
    assert "client_id=cid" in sent
    assert "client_secret=csecret" in sent
    # Никакой ссылки на клиента — в отличие от agency_client_credentials.
    assert "agency_client_id" not in sent
    assert "agency_client_name" not in sent


@respx.mock
def test_own_account_token_sends_form_encoded_body() -> None:
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
    _run(request_own_account_token("cid", "csecret"))
    request = route.calls.last.request
    assert request.headers["Content-Type"] == "application/x-www-form-urlencoded"


def test_own_account_token_empty_client_id_is_not_configured() -> None:
    with pytest.raises(VkOAuthNotConfigured):
        _run(request_own_account_token("", "csecret"))


def test_own_account_token_empty_client_secret_is_not_configured() -> None:
    with pytest.raises(VkOAuthNotConfigured):
        _run(request_own_account_token("cid", ""))


def test_own_account_token_not_configured_checked_before_network_call() -> None:
    # Ни одного respx-мока не зарегистрировано: сеть не должна затрагиваться вовсе.
    with pytest.raises(VkOAuthNotConfigured):
        _run(request_own_account_token("", ""))


@respx.mock
def test_own_account_token_invalid_credentials() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(401, json={"error": "invalid_client"}))
    with pytest.raises(VkOAuthInvalidCredentials):
        _run(request_own_account_token("bad-cid", "bad-csecret"))


@respx.mock
def test_own_account_token_rejected_by_substance() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(VkOAuthRejected):
        _run(request_own_account_token("cid", "csecret"))


@respx.mock
def test_own_account_token_server_error_is_unavailable() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(VkOAuthUnavailable):
        _run(request_own_account_token("cid", "csecret"))


@respx.mock
def test_own_account_token_network_failure_is_unavailable() -> None:
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(VkOAuthUnavailable):
        _run(request_own_account_token("cid", "csecret"))


@respx.mock
def test_own_account_token_secret_not_leaked_into_error() -> None:
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(VkOAuthUnavailable) as err:
        _run(request_own_account_token("cid", "super-secret-client-secret"))
    assert "super-secret-client-secret" not in str(err.value)


# --- Обновление токена -------------------------------------------------------


@respx.mock
def test_refresh_token_returns_new_token() -> None:
    route = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
    token = _run(refresh_agency_token("old-refresh", "cid", "csecret"))
    assert token.access_token.get_secret_value() == "issued-access-token"
    sent = route.calls.last.request.content.decode()
    assert "grant_type=refresh_token" in sent
    assert "refresh_token=old-refresh" in sent
    assert "client_id=cid" in sent
    assert "client_secret=csecret" in sent


@respx.mock
def test_refresh_rejected_grant_is_rejected_error() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(VkOAuthRejected):
        _run(refresh_agency_token("expired-refresh", "cid", "csecret"))


# --- Удаление токена ----------------------------------------------------------


@respx.mock
def test_delete_tokens_sends_expected_body() -> None:
    route = respx.post(TOKEN_DELETE_URL).mock(return_value=httpx.Response(200, json={}))
    _run(delete_agency_tokens("cid", "csecret", "client_username"))
    sent = route.calls.last.request.content.decode()
    assert "client_id=cid" in sent
    assert "client_secret=csecret" in sent
    assert "username=client_username" in sent


@respx.mock
def test_delete_tokens_does_not_raise_on_success() -> None:
    respx.post(TOKEN_DELETE_URL).mock(return_value=httpx.Response(200, json={}))
    _run(delete_agency_tokens("cid", "csecret", "client_username"))


def test_delete_tokens_requires_username() -> None:
    with pytest.raises(ValueError):
        _run(delete_agency_tokens("cid", "csecret", ""))


# --- Не сконфигурировано ------------------------------------------------------


def test_empty_client_id_is_not_configured() -> None:
    with pytest.raises(VkOAuthNotConfigured):
        _run(request_agency_client_token("", "csecret", agency_client_id="1"))


def test_empty_client_secret_is_not_configured() -> None:
    with pytest.raises(VkOAuthNotConfigured):
        _run(request_agency_client_token("cid", "", agency_client_id="1"))


def test_not_configured_checked_before_network_call() -> None:
    # Ни одного respx-мока не зарегистрировано: сеть не должна затрагиваться вовсе.
    with pytest.raises(VkOAuthNotConfigured):
        _run(delete_agency_tokens("", "", "user"))


# --- Ошибка учётных данных vs отказ VK по существу vs сетевой сбой -----------


@respx.mock
def test_invalid_client_is_invalid_credentials() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(401, json={"error": "invalid_client"}))
    with pytest.raises(VkOAuthInvalidCredentials):
        _run(request_agency_client_token("bad-cid", "bad-csecret", agency_client_id="1"))


@respx.mock
def test_400_invalid_client_is_also_invalid_credentials() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_client"}))
    with pytest.raises(VkOAuthInvalidCredentials):
        _run(request_agency_client_token("cid", "csecret", agency_client_id="1"))


@respx.mock
def test_unknown_agency_client_is_rejected_not_invalid_credentials() -> None:
    """Неверный client_id/secret и неверная ссылка на клиента — разные отказы."""
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(400, json={"error": "invalid_grant"}))
    with pytest.raises(VkOAuthRejected):
        _run(request_agency_client_token("cid", "csecret", agency_client_id="does-not-exist"))


@respx.mock
def test_server_error_is_unavailable() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(503))
    with pytest.raises(VkOAuthUnavailable):
        _run(request_agency_client_token("cid", "csecret", agency_client_id="1"))


@respx.mock
def test_network_failure_is_unavailable() -> None:
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("down"))
    with pytest.raises(VkOAuthUnavailable):
        _run(request_agency_client_token("cid", "csecret", agency_client_id="1"))


@respx.mock
def test_timeout_is_unavailable() -> None:
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectTimeout("timeout"))
    with pytest.raises(VkOAuthUnavailable):
        _run(refresh_agency_token("refresh", "cid", "csecret"))


@respx.mock
def test_non_json_body_is_unavailable() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))
    with pytest.raises(VkOAuthUnavailable):
        _run(request_agency_client_token("cid", "csecret", agency_client_id="1"))


@respx.mock
def test_missing_access_token_is_unavailable() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json={"refresh_token": "r", "expires_in": "86400"})
    )
    with pytest.raises(VkOAuthUnavailable):
        _run(request_agency_client_token("cid", "csecret", agency_client_id="1"))


# --- Секрет не попадает в текст исключения -----------------------------------


@respx.mock
def test_client_secret_not_leaked_into_network_error() -> None:
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(VkOAuthUnavailable) as err:
        _run(request_agency_client_token("cid", "super-secret-client-secret", agency_client_id="1"))
    assert "super-secret-client-secret" not in str(err.value)


@respx.mock
def test_client_secret_not_leaked_into_rejection_error() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(401, json={"error": "invalid_client"}))
    with pytest.raises(VkOAuthInvalidCredentials) as err:
        _run(request_agency_client_token("cid", "super-secret-client-secret", agency_client_id="1"))
    assert "super-secret-client-secret" not in str(err.value)


@respx.mock
def test_refresh_token_not_leaked_into_exception() -> None:
    respx.post(TOKEN_URL).mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(VkOAuthUnavailable) as err:
        _run(refresh_agency_token("super-secret-refresh-token", "cid", "csecret"))
    assert "super-secret-refresh-token" not in str(err.value)


@respx.mock
def test_issued_secrets_not_leaked_into_repr() -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN_RESPONSE))
    token = _run(request_agency_client_token("cid", "csecret", agency_client_id="1"))
    assert "issued-access-token" not in repr(token)
    assert "issued-refresh-token" not in repr(token)
