"""Тесты бот-команды `/stop_campaign <id>` (операторская остановка кампании).

Хендлер тонкий: аргумент → `api_client.stop_campaign` → человеческий ответ.
Проверяем разбор аргумента и все ветки ответов (успех, кампания без внешнего id,
кампании нет, канал не принял остановку, ядро недоступно).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from aiogram.filters import CommandObject
from bot.api_client import CampaignNotFound, CampaignStopFailed, CampaignStopped, CoreUnavailable
from bot.handlers import stop_campaign as handler


class _FakeMessage:
    def __init__(self) -> None:
        self.answers: list[str] = []

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)


def _command(args: str | None) -> CommandObject:
    return CommandObject(prefix="/", command="stop_campaign", args=args)


def _run(args: str | None, monkeypatch: pytest.MonkeyPatch, stopper: Any) -> str:
    monkeypatch.setattr("bot.handlers.stop_campaign.api_client.stop_campaign", stopper)
    message = _FakeMessage()
    asyncio.run(handler.stop_campaign(message, _command(args)))
    assert message.answers
    return message.answers[0]


async def _ok(campaign_id: int) -> CampaignStopped:
    return CampaignStopped(campaign_id=campaign_id, status="stopped", external_id="ext-7")


async def _ok_without_external(campaign_id: int) -> CampaignStopped:
    return CampaignStopped(campaign_id=campaign_id, status="stopped", external_id=None)


def test_missing_argument_shows_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _unused(campaign_id: int) -> CampaignStopped:
        raise AssertionError("ядро дёргать нельзя без номера кампании")

    text = _run(None, monkeypatch, _unused)
    assert "/stop_campaign" in text


def test_non_numeric_argument_shows_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _unused(campaign_id: int) -> CampaignStopped:
        raise AssertionError("ядро дёргать нельзя без корректного номера")

    text = _run("абв", monkeypatch, _unused)
    assert "/stop_campaign" in text


def test_successful_stop_reports_campaign(monkeypatch: pytest.MonkeyPatch) -> None:
    text = _run("7", monkeypatch, _ok)
    assert "7" in text
    assert "останов" in text.lower()


def test_stop_without_external_id_is_honest(monkeypatch: pytest.MonkeyPatch) -> None:
    # Кампания жила только у нас: статус меняем, но честно говорим, что площадку
    # останавливать было нечего (CLAUDE.md §7 — успех не имитируем).
    text = _run("7", monkeypatch, _ok_without_external)
    assert "площадк" in text.lower()


def test_unknown_campaign_reports_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _missing(campaign_id: int) -> CampaignStopped:
        raise CampaignNotFound(str(campaign_id))

    text = _run("7", monkeypatch, _missing)
    assert "не найдена" in text.lower()


def test_channel_failure_reports_platform_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _failed(campaign_id: int) -> CampaignStopped:
        raise CampaignStopFailed("boom")

    text = _run("7", monkeypatch, _failed)
    assert "канал" in text.lower()


def test_core_unavailable_reports_service_down(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _down(campaign_id: int) -> CampaignStopped:
        raise CoreUnavailable("boom")

    text = _run("7", monkeypatch, _down)
    assert "недоступен" in text.lower()
