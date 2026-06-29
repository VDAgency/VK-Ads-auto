import pytest
from services.contact import Contact, ContactParseError, ContactType, detect_contact


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("user@example.com", Contact(ContactType.EMAIL, "user@example.com")),
        ("  User@Example.COM ", Contact(ContactType.EMAIL, "user@example.com")),
        ("+7 (999) 123-45-67", Contact(ContactType.PHONE, "+79991234567")),
        ("89991234567", Contact(ContactType.PHONE, "+79991234567")),
        ("9991234567", Contact(ContactType.PHONE, "+79991234567")),
        ("@durov", Contact(ContactType.TELEGRAM, "@durov")),
        ("https://t.me/durov", Contact(ContactType.TELEGRAM, "@durov")),
        ("durov_channel", Contact(ContactType.TELEGRAM, "@durov_channel")),
    ],
)
def test_detect_contact(raw: str, expected: Contact) -> None:
    assert detect_contact(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "not a contact!", "12345", "@ab"])
def test_detect_contact_rejects_garbage(raw: str) -> None:
    with pytest.raises(ContactParseError):
        detect_contact(raw)
