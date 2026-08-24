"""Разбор ответа VK `groups.getCallbackServers` на предмет подключённого Senler.

Разведка 2026-08-24: сообщество с подключённым чат-ботом Senler возвращает ровно
один callback-сервер с адресом на домене `senler.ru` и статусом `ok`. Определяем
Senler по домену адреса, а не по названию — название редактирует владелец
сообщества в интерфейсе VK и оно ничего не гарантирует. Чистые функции, без сети.
"""

from __future__ import annotations

from services.senler import detect_senler


def test_senler_is_detected_by_its_callback_server() -> None:
    servers = [
        {
            "id": 2,
            "title": "Senler",
            "url": "https://callback.senler.ru/webhook/vk/1078808",
            "status": "ok",
        }
    ]
    check = detect_senler(servers)
    assert check.connected is True
    assert check.project_id == "1078808"


def test_other_callback_servers_do_not_count_as_senler() -> None:
    servers = [{"title": "Мой сервер", "url": "https://example.com/hook", "status": "ok"}]
    assert detect_senler(servers).connected is False


def test_broken_senler_server_is_not_a_working_funnel() -> None:
    servers = [
        {"title": "Senler", "url": "https://callback.senler.ru/webhook/vk/1", "status": "failed"}
    ]
    assert detect_senler(servers).connected is False


def test_no_callback_servers_means_not_connected() -> None:
    """Сообщество вообще без callback-серверов — Senler точно не подключён."""
    check = detect_senler([])
    assert check.connected is False
    assert check.project_id is None
    assert check.reason


def test_senler_is_found_among_unrelated_servers() -> None:
    """Название чужого сервера маскируется под Senler — определяем по домену, не title."""
    servers = [
        {"title": "Senler", "url": "https://example.com/senler-lookalike", "status": "ok"},
        {
            "title": "Обычный вебхук",
            "url": "https://callback.senler.ru/webhook/vk/42",
            "status": "ok",
        },
    ]
    check = detect_senler(servers)
    assert check.connected is True
    assert check.project_id == "42"


def test_reason_explains_why_disconnected_for_operator() -> None:
    """Отказ несёт человекочитаемую причину — не пустую строку."""
    check = detect_senler(
        [{"title": "Senler", "url": "https://callback.senler.ru/webhook/vk/1", "status": "failed"}]
    )
    assert check.connected is False
    assert "failed" in check.reason
