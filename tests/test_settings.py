import os
from collections.abc import Iterator

import pytest
from config.settings import Settings, get_settings


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
