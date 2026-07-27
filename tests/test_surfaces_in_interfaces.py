"""Площадка подписки доезжает до обоих интерфейсов: бота и веб-кабинета.

Клиент выбирает площадку словами, а их шесть. Непонятое значение молча трактуется как
сообщество — значит распознанный вариант обязан быть виден оператору ДО запуска.
Разбор живёт в ядре в одном экземпляре (`BriefCardView.surface_title`), интерфейсы его
только показывают; здесь закрепляем и разбор, и показ.
"""

from __future__ import annotations

from bot.api_client import BriefCard, BriefFieldItem
from bot.handlers.brief_card import _render_card
from bot.handlers.surfaces import render_surfaces
from bot.menu import bot_commands
from services.brief_view import BriefCardView, BriefFieldView
from services.goals import subscription_targets


def _card(surface_title: str) -> BriefCard:
    return BriefCard(
        brief_id=42,
        variant="individual",
        status="received",
        client_name="Иван Иванов",
        client_email="i@example.com",
        client_phone="+70000000000",
        client_telegram=None,
        fields=[BriefFieldItem(n=8, label="Куда привлекаем", value="рассылка")],
        has_creative=True,
        campaign_status=None,
        surface_title=surface_title,
    )


def test_brief_card_shows_the_resolved_surface() -> None:
    text = _render_card(_card("Рассылка ВКонтакте"))
    assert "🎯 Площадка: Рассылка ВКонтакте" in text
    assert "/surfaces" in text, "оператору нужен способ узнать допустимые варианты"


def test_brief_card_without_surface_stays_readable() -> None:
    text = _render_card(_card(""))
    assert "🎯 Площадка" not in text
    assert "Креатив" in text


def test_surfaces_command_lists_every_surface() -> None:
    text = render_surfaces()
    for target in subscription_targets():
        assert target.title in text, target.title
        assert target.kind in text, "оператору нужно знать, что писать в бриф"


def test_surfaces_command_is_in_the_menu() -> None:
    assert "surfaces" in {command.command for command in bot_commands()}


def test_card_view_carries_the_surface_for_both_interfaces() -> None:
    # Один и тот же разбор питает и бота, и веб-кабинет — дублировать его в TS нельзя.
    view = BriefCardView(
        brief_id=1,
        variant="community",
        status="received",
        client_name=None,
        client_email=None,
        client_phone=None,
        client_telegram=None,
        fields=[BriefFieldView(number=1, label="Куда привлекаем", value="профиль в ОК")],
        has_creative=False,
        campaign_status=None,
        surface_title="Профиль в Одноклассниках",
    )
    assert view.surface_title == "Профиль в Одноклассниках"
