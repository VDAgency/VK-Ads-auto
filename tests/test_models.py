from db.models import Account, IntegrationConfig, Operator


def test_tenant_tables_have_account_id() -> None:
    assert "account_id" in Operator.__table__.columns
    assert "account_id" in IntegrationConfig.__table__.columns


def test_account_id_is_not_nullable() -> None:
    assert Operator.__table__.columns["account_id"].nullable is False


def test_account_root_has_no_account_id() -> None:
    # Account — корень изоляции, сам по тенанту не скоупится.
    assert "account_id" not in Account.__table__.columns


def test_integration_config_default_channel() -> None:
    # Конфиг интеграций — per-account, с каналом по умолчанию.
    assert IntegrationConfig.__table__.columns["default_channel"].default.arg == "vk_api"
