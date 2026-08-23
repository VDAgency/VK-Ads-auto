"""Тесты заглушки адаптера (`integrations.stub.StubAdapter`) — без сети, без мутаций."""

from __future__ import annotations

import asyncio

from integrations.stub import StubAdapter


def test_delete_campaign_is_a_noop() -> None:
    # Заглушка ничего не мутировала во внешней системе — удалять нечего.
    assert asyncio.run(StubAdapter().delete_campaign("stub-campaign-1")) is None
