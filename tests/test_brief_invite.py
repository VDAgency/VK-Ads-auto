from services.brief_invite import (
    DeliveryChannel,
    brief_url,
    compose_invite,
    delivery_channel,
)
from services.brief_parser import BriefVariant
from services.contact import Contact, ContactType


def test_brief_url_by_variant() -> None:
    assert (
        brief_url(BriefVariant.INDIVIDUAL, "https://vk-ads-auto.ru")
        == "https://vk-ads-auto.ru/brief-individual.html"
    )
    assert (
        brief_url(BriefVariant.COMMUNITY, "https://vk-ads-auto.ru/")
        == "https://vk-ads-auto.ru/brief-community.html"
    )


def test_delivery_channel_by_contact_type() -> None:
    assert delivery_channel(Contact(ContactType.EMAIL, "a@b.c")) is DeliveryChannel.EMAIL
    assert delivery_channel(Contact(ContactType.TELEGRAM, "@u")) is DeliveryChannel.TELEGRAM
    assert delivery_channel(Contact(ContactType.PHONE, "+79991234567")) is DeliveryChannel.MANUAL


def test_compose_invite_contains_link() -> None:
    text = compose_invite(BriefVariant.INDIVIDUAL, "https://vk-ads-auto.ru")
    assert "https://vk-ads-auto.ru/brief-individual.html" in text
