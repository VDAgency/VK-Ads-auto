from db import models  # noqa: F401 -- регистрирует модели в Base.metadata
from db.base import Base
from sqlalchemy import create_engine, inspect


def test_metadata_creates_all_tables() -> None:
    # Модели должны давать согласованную схему (проверяем на in-memory SQLite).
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    tables = set(inspect(engine).get_table_names())
    assert {"account", "operator", "integration_config"} <= tables


def test_tenant_tables_carry_account_id() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    inspector = inspect(engine)
    for table in ("operator", "integration_config"):
        cols = {c["name"] for c in inspector.get_columns(table)}
        assert "account_id" in cols
