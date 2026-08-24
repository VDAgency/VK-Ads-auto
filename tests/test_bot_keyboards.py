"""Клавиатура выбора клиента (`client_pick_keyboard`): постраничный список +
быстрый выбор «оставить общим» — общая для привязки при добавлении кабинета и
для перепривязки уже заведённого (bot/handlers/ad_accounts.py)."""

from __future__ import annotations

from bot.keyboards import client_pick_keyboard, creative_confirm_keyboard, launch_confirm_keyboard


def _buttons(markup: object) -> list[tuple[str, str]]:
    return [
        (b.text, b.callback_data)
        for row in markup.inline_keyboard  # type: ignore[attr-defined]
        for b in row
    ]


def test_first_page_offers_general_option_first() -> None:
    items = [(1, "Иван"), (2, "Пётр")]
    markup = client_pick_keyboard(items, 0, "addclient")
    buttons = _buttons(markup)
    assert buttons[0] == ("🌐 Оставить общим", "addclient:none")


def test_all_items_fit_on_one_page_when_short() -> None:
    items = [(1, "Иван"), (2, "Пётр")]
    markup = client_pick_keyboard(items, 0, "addclient")
    datas = [data for _text, data in _buttons(markup)]
    assert "addclient:1" in datas
    assert "addclient:2" in datas
    # Меньше страницы целиком — кнопок пролистывания нет.
    assert not any(data.startswith("addclient:pg:") for data in datas)


def test_long_list_is_split_into_pages() -> None:
    items = [(i, f"Клиент {i}") for i in range(1, 10)]  # 9 клиентов
    markup = client_pick_keyboard(items, 0, "addclient")
    datas = [data for _text, data in _buttons(markup)]
    # Первая страница не показывает девятого клиента, зато даёт кнопку «дальше».
    assert "addclient:9" not in datas
    assert "addclient:pg:1" in datas


def test_second_page_shows_the_rest_and_a_way_back() -> None:
    items = [(i, f"Клиент {i}") for i in range(1, 10)]
    markup = client_pick_keyboard(items, 1, "addclient")
    datas = [data for _text, data in _buttons(markup)]
    assert "addclient:9" in datas
    assert "addclient:pg:0" in datas
    assert not any(data.startswith("addclient:pg:") and data != "addclient:pg:0" for data in datas)


def test_action_prefix_carries_context_for_rebind_flow() -> None:
    """Перепривязка существующего кабинета кодирует его id прямо в `action`."""
    items = [(1, "Иван")]
    markup = client_pick_keyboard(items, 0, "adaccbind:42")
    datas = [data for _text, data in _buttons(markup)]
    assert "adaccbind:42:none" in datas
    assert "adaccbind:42:1" in datas


def test_cancel_button_is_always_present() -> None:
    markup = client_pick_keyboard([], 0, "addclient")
    datas = [data for _text, data in _buttons(markup)]
    assert "adacc:cancel" in datas


# --- Одно действие — один глагол (ревью операторского опыта §2.1) --------------
#
# Обе кнопки подтверждения запуска тратят бюджет клиента, но раньше назывались
# по-разному («✅ Отправить» / «🚀 Запустить»). К моменту подтверждения оператор
# уже видел «Отправить» для медиа и текста — третье значение того же слова
# путает. Обе кнопки должны называться одинаково: «🚀 Запустить».


def test_both_confirmation_buttons_use_the_same_launch_label() -> None:
    creative_buttons = _buttons(creative_confirm_keyboard())
    launch_buttons = _buttons(launch_confirm_keyboard(brief_id=7, ad_account_id=3))

    creative_confirm_text = next(text for text, data in creative_buttons if data == "creative_send")
    launch_confirm_text = next(text for text, data in launch_buttons if data == "nocre_confirm:7:3")
    assert creative_confirm_text == launch_confirm_text == "🚀 Запустить"
