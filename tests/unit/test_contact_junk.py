import pytest

from leadbeast.contacts.verify import syntax_ok


@pytest.mark.parametrize("email", [
    "user@domain.com",  # website template placeholder
    "email@email.com",
    "yourname@yourdomain.com",
    "c244f93d986d9a3a8bbd9db4e3792582@acceptance.visa.com",  # payment processor
    "frontdesk@harborsmile.example",
    "noreply@shop.com",
])
def test_placeholder_and_processor_emails_are_junk(email: str) -> None:
    assert not syntax_ok(email)


@pytest.mark.parametrize("email", [
    "office@lajollacolonydental.com", "info@delahcoffee.com", "nomadcafe6500@gmail.com",
    "community@blackoakcoffee.com",
])
def test_real_business_emails_pass(email: str) -> None:
    assert syntax_ok(email)
