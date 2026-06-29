"""Минимальные модели данных со швами мульти-тенанта (Фаза 0).

Полная модель данных разворачивается в последующих фазах (см. PROJECT.md §6).
Здесь заложены инварианты: `account_id` на тенант-скоупленных таблицах и
per-account конфиг интеграций (ключи — в окружении, не глобальные константы).
"""

from __future__ import annotations

from sqlalchemy import BigInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TenantMixin


class Account(Base):
    """Тенант — владелец инстанса данных. Корень изоляции (без `account_id`)."""

    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))


class Operator(TenantMixin, Base):
    """Оператор (Анастасия) — пользователь, ставящий задачи; принадлежит Account."""

    __tablename__ = "operator"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    full_name: Mapped[str | None] = mapped_column(String(255), default=None)


class IntegrationConfig(TenantMixin, Base):
    """Per-account конфиг интеграций: канал по умолчанию и состояние health-check.

    Сами секреты (ключи VK/бот/Google/Senler) живут в окружении/секретах, а не в
    этой таблице — здесь только привязанные к тенанту параметры выбора канала.
    """

    __tablename__ = "integration_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    default_channel: Mapped[str] = mapped_column(String(32), default="vk_api")
    channel_healthy: Mapped[bool] = mapped_column(default=True)
