"""Минимальные модели данных со швами мульти-тенанта (Фаза 0).

Полная модель данных разворачивается в последующих фазах (см. PROJECT.md §6).
Здесь заложены инварианты: `account_id` на тенант-скоупленных таблицах и
per-account конфиг интеграций (ключи — в окружении, не глобальные константы).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TenantMixin


class Account(Base):
    """Тенант — владелец инстанса данных. Корень изоляции (без `account_id`)."""

    __tablename__ = "account"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))


class Operator(TenantMixin, Base):
    """Оператор — пользователь, ставящий задачи; принадлежит Account."""

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


class Client(TenantMixin, Base):
    """Клиент оператора. Идентифицируется по любому из 3 контактов (email/phone/telegram).

    `is_self` — флаг «это сам оператор как клиент своей рекламы» (PROJECT.md §6).
    """

    __tablename__ = "client"

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(255), default=None)
    email: Mapped[str | None] = mapped_column(String(255), index=True, default=None)
    phone: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    telegram: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    is_self: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Brief(TenantMixin, Base):
    """Бриф клиента: сырой ответ формы + вариант (физлицо/сообщество) + статус.

    Структурированный разбор делает `services.brief_parser`; здесь хранится исходный
    `payload` (как пришёл с веб-формы/бота) и связь с клиентом по контактам.
    """

    __tablename__ = "brief"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("client.id"), index=True, default=None)
    variant: Mapped[str] = mapped_column(String(32))  # individual | community
    status: Mapped[str] = mapped_column(String(32), default="received")
    source: Mapped[str] = mapped_column(String(32), default="web")  # web | bot
    payload: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
