"""Базовые декларативные классы SQLAlchemy и шов мульти-тенантности.

`TenantMixin` добавляет `account_id` любой таблице, скоупящейся по тенанту. Сам
`Account` его не наследует (он и есть тенант). Все запросы к тенант-скоупленным
таблицам обязаны фильтроваться по `account_id` (изоляция строк по тенанту).
"""

from __future__ import annotations

from sqlalchemy import ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Общий декларативный базовый класс для всех моделей."""


class TenantMixin:
    """Шов мульти-тенанта: `account_id` (NOT NULL, индексируемый) на таблице."""

    account_id: Mapped[int] = mapped_column(
        ForeignKey("account.id"),
        index=True,
        nullable=False,
    )
