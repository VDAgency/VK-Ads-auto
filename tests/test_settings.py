import os
from collections.abc import Iterator

import pytest
from config.settings import Settings, get_settings
from pydantic import SecretStr


@pytest.fixture(autouse=True)
def _clear_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_default_app_env_is_local() -> None:
    settings = Settings(_env_file=None)
    assert settings.app_env == "local"


def test_app_env_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    settings = Settings(_env_file=None)
    assert settings.app_env == "production"


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_no_secret_baked_into_defaults() -> None:
    # Дефолты не должны содержать секретов (пароли/токены приходят только из env).
    assert "BOT_TOKEN" not in os.environ.get("DATABASE_URL", "")
    settings = Settings(_env_file=None)
    assert "password" not in settings.database_url.lower()


def test_token_fields_default_empty() -> None:
    settings = Settings(_env_file=None)
    assert settings.bot_token.get_secret_value() == ""
    assert settings.vk_ads_access_token.get_secret_value() == ""
    assert settings.vk_ads_refresh_token.get_secret_value() == ""
    assert settings.vk_ads_token_type == "Bearer"
    assert settings.operator_telegram_ids == frozenset()


def test_tokens_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "test-bot-token")
    monkeypatch.setenv("VK_ADS_ACCESS_TOKEN", "vk-access")
    settings = Settings(_env_file=None)
    assert settings.bot_token.get_secret_value() == "test-bot-token"
    assert settings.vk_ads_access_token.get_secret_value() == "vk-access"


def test_operator_ids_parsed_from_csv(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPERATOR_TELEGRAM_IDS", "5389520473, 5481870843")
    settings = Settings(_env_file=None)
    assert settings.operator_telegram_ids == frozenset({5389520473, 5481870843})


def test_is_operator() -> None:
    settings = Settings(_env_file=None, operator_telegram_ids=frozenset({111, 222}))
    assert settings.is_operator(111) is True
    assert settings.is_operator(999) is False


def test_secret_not_in_repr() -> None:
    # SecretStr не должен раскрывать значение в repr/str (защита от утечки в логи).
    settings = Settings(_env_file=None, bot_token=SecretStr("super-secret"))
    assert "super-secret" not in repr(settings)
    assert "super-secret" not in str(settings.bot_token)
