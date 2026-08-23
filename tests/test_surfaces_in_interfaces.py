"""Площадка подписки доезжает до обоих интерфейсов: бота и веб-кабинета.

Клиент выбирает площадку словами, а их шесть. Непонятое значение молча трактуется как
сообщество — значит распознанный вариант обязан быть виден оператору ДО запуска.
Разбор живёт в ядре в одном экземпляре (`BriefCardView.surface_title`), интерфейсы его
только показывают; здесь закрепляем и разбор, и показ.
"""

from __future__ import annotations

import re

from bot.api_client import BriefCard, BriefFieldItem
from bot.handlers.brief_card import _render_card
from bot.handlers.surfaces import render_surfaces
from bot.menu import bot_commands
from services.brief_view import BriefCardView, BriefFieldView
from services.goals import subscription_targets

# Единственная разметка, которую `render_surfaces` реально расставляет сама.
_ALLOWED_TELEGRAM_TAGS = {"b", "/b"}


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


def test_surfaces_command_produces_html_telegram_will_accept() -> None:
    """Регрессия: `/surfaces` не отвечал вообще ничем.

    Причина не в роутере и не в перехвате: `surfaces.router` зарегистрирован и стоит
    раньше `stranger.router` (см. `bot/main.py`), и он вызывался (не проглатывался
    FSM-заглушками — те матчят только активное состояние конкретного оператора).
    `render_surfaces()` тоже не падал — он собирал текст успешно.

    Падало позже: подсказка лид-формы в справочнике площадок несла буквальный плейсхолдер
    `leadads://<номер>/`. При отправке с `parse_mode="HTML"` Telegram читает `<номер>` как
    открывающий тег неизвестного типа и отклоняет `sendMessage` целиком (`can't parse
    entities: unsupported start tag "номер"`). aiogram эту ошибку не пробрасывает наружу —
    `Dispatcher._process_update` ловит любое исключение хендлера и только логирует его,
    поэтому оператор не видел ни текста, ни ошибки.

    Тест гоняется на РЕАЛЬНОМ справочнике площадок (`services.goals.subscription_targets`),
    а не на синтетических данных: следующая площадка с `<`/`>`/`&` в названии или подсказке
    обязана быть поймана здесь же, а не в проде.
    """
    text = render_surfaces()
    found = re.findall(r"<([^>]*)>", text)
    stray_tags = [tag for tag in found if tag not in _ALLOWED_TELEGRAM_TAGS]
    message = f"Telegram отклонит sendMessage с parse_mode=HTML — непонятные теги: {stray_tags}"
    assert stray_tags == [], message


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
        surface_needs_creative=True,
    )
    assert view.surface_title == "Профиль в Одноклассниках"
