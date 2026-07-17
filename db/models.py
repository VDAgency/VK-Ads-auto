"""Минимальные модели данных со швами мульти-тенанта (Фаза 0).

Полная модель данных разворачивается в последующих фазах (см. PROJECT.md §6).
Здесь заложены инварианты: `account_id` на тенант-скоупленных таблицах и
per-account конфиг интеграций (ключи — в окружении, не глобальные константы).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Index, String, func
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
    """Клиент оператора. Идентифицируется по контактам; email уникален в рамках тенанта.

    `is_self` — флаг «это сам оператор как клиент своей рекламы» (PROJECT.md §6).
    Пароль (кабинет, spec 2026-07-17) — только хеш; `null` = ещё не установлен.
    Уникальность `(account_id, email)`: NULL-email не конфликтуют (стандартная семантика).
    """

    __tablename__ = "client"
    __table_args__ = (Index("uq_client_account_email", "account_id", "email", unique=True),)

    id: Mapped[int] = mapped_column(primary_key=True)
    full_name: Mapped[str | None] = mapped_column(String(255), default=None)
    email: Mapped[str | None] = mapped_column(String(255), index=True, default=None)
    phone: Mapped[str | None] = mapped_column(String(32), index=True, default=None)
    telegram: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    is_self: Mapped[bool] = mapped_column(default=False)
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    password_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Brief(TenantMixin, Base):
    """Бриф клиента: сырой ответ формы + вариант (физлицо/сообщество) + статус.

    Структурированный разбор делает `services.brief_parser`; здесь хранится исходный
    `payload` (как пришёл с веб-формы/бота) и связь с клиентом по контактам. Поле
    `invite_id` заполняется, если бриф пришёл по токен-ссылке, которую оператор
    заранее отправил через `BriefInvite` (см. spec 2026-07-13).
    """

    __tablename__ = "brief"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("client.id"), index=True, default=None)
    variant: Mapped[str] = mapped_column(String(32))  # individual | community
    status: Mapped[str] = mapped_column(String(32), default="received")
    source: Mapped[str] = mapped_column(String(32), default="web")  # web | bot
    payload: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    invite_id: Mapped[int | None] = mapped_column(
        ForeignKey("brief_invite.id"), index=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BriefInvite(TenantMixin, Base):
    """Отправленное клиенту приглашение заполнить бриф (см. spec 2026-07-13).

    Один инвайт — одна отправка. Токен уникален и вшит в URL формы; при приёме
    брифа мы находим инвайт по токену и метим его `received`. Статусы: `pending`
    (создан, доставка ещё не начата), `sent` (доставлен через канал), `failed`
    (канал сорвался, оператор получил fallback-текст), `received` (клиент прислал
    бриф), `superseded` (заменён более новым инвайтом того же клиента с той же
    ошибкой). См. §4 спеки для статус-машины.
    """

    __tablename__ = "brief_invite"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    variant: Mapped[str] = mapped_column(String(32))  # individual | community
    contact_type: Mapped[str] = mapped_column(String(16))  # email | phone | telegram
    contact_value: Mapped[str] = mapped_column(String(255), index=True)
    # Имя получателя, добытое каналом в момент отправки (Telegram first/last name).
    # None для email/phone и записей до появления фичи. Для читаемого «Ждём бриф».
    contact_name: Mapped[str | None] = mapped_column(String(255), default=None)
    channel: Mapped[str] = mapped_column(String(16))  # telegram | email | manual
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # pending | sent | failed | received | superseded
    error: Mapped[str | None] = mapped_column(String(500), default=None)
    operator_id: Mapped[int] = mapped_column(ForeignKey("operator.id"), index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("client.id"), index=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Creative(TenantMixin, Base):
    """Креатив, загруженный оператором под бриф: медиа на диске + заголовок/текст.

    Отправка креатива — триггер создания рекламной кампании (см. spec 2026-07-17).
    Файл лежит в volume РФ-сервера (`CREATIVES_DIR`), в БД — путь и метаданные.
    """

    __tablename__ = "creative"

    id: Mapped[int] = mapped_column(primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("brief.id"), index=True)
    media_type: Mapped[str] = mapped_column(String(16))  # photo | video
    file_path: Mapped[str] = mapped_column(String(500))
    title: Mapped[str | None] = mapped_column(String(255), default=None)
    body: Mapped[str | None] = mapped_column(String(1000), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Campaign(TenantMixin, Base):
    """Рекламная кампания, созданная по брифу.

    `status`: `prepared` (подготовлена; боевой запуск VK заблокирован агентским
    статусом ИП, CLAUDE.md §1.4), `launched` (реально запущена в VK), `failed`.
    `external_id` — id кампании во внешней площадке (или синтетический у заглушки).
    `spec_json` — сериализованная `CampaignSpec` (раскладка брифа).
    """

    __tablename__ = "campaign"

    id: Mapped[int] = mapped_column(primary_key=True)
    brief_id: Mapped[int] = mapped_column(ForeignKey("brief.id"), index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("client.id"), index=True, default=None)
    status: Mapped[str] = mapped_column(String(16), default="prepared")
    objective: Mapped[str] = mapped_column(String(32))
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    external_id: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    launched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class Stat(TenantMixin, Base):
    """Срез метрик кампании на момент времени (для отчётов и дайджеста)."""

    __tablename__ = "stat"

    id: Mapped[int] = mapped_column(primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(64), index=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    shows: Mapped[float] = mapped_column(default=0.0)
    clicks: Mapped[float] = mapped_column(default=0.0)
    spent: Mapped[float] = mapped_column(default=0.0)
    results: Mapped[float] = mapped_column(default=0.0)


class Referral(TenantMixin, Base):
    """Кто кого привёл (одноуровневая рефералка, Блок 2)."""

    __tablename__ = "referral"

    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_client_id: Mapped[int] = mapped_column(ForeignKey("client.id"), index=True)
    referred_client_id: Mapped[int] = mapped_column(ForeignKey("client.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Discount(TenantMixin, Base):
    """Скидка реферера на 1 месяц (задел под автоматизацию платежей)."""

    __tablename__ = "discount"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("client.id"), index=True)
    percent: Mapped[int] = mapped_column(default=0)
    month: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | applied
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
