import smtplib
from typing import Any, ClassVar

import pytest

from cocat.app.mail import (
    PLACEHOLDER_DOMAIN,
    Mail,
    MailError,
    invitation_mail,
    is_placeholder,
    reset_mail,
    verification_mail,
)

pytestmark = pytest.mark.anyio


class FakeSMTP:
    instances: ClassVar[list["FakeSMTP"]] = []

    def __init__(self, host: str, port: int, timeout: float | None = None) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.messages: list[Any] = []
        FakeSMTP.instances.append(self)

    def close(self) -> None:
        pass

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, username: str, password: str) -> None:
        self.logged_in = (username, password)

    def send_message(self, message: Any) -> None:
        self.messages.append(message)


@pytest.fixture()
def smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instances = []
    monkeypatch.setattr("cocat.app.mail.smtplib.SMTP", FakeSMTP)
    return FakeSMTP


def configured(**overrides: str) -> dict[str, str]:
    environ = {
        "COCAT_SMTP_HOST": "smtp.example.org",
        "COCAT_MAIL_FROM": "cocat@example.org",
    }
    environ.update(overrides)
    return environ


def test_nothing_configured_means_no_sender() -> None:
    assert Mail.from_env({}) is None


def test_a_blank_host_means_no_sender() -> None:
    assert Mail.from_env({"COCAT_SMTP_HOST": "   "}) is None


def test_a_host_without_a_sender_is_refused() -> None:
    with pytest.raises(ValueError, match="COCAT_MAIL_FROM"):
        Mail.from_env({"COCAT_SMTP_HOST": "smtp.example.org"})


def test_the_full_configuration_is_read() -> None:
    mail = Mail.from_env(
        configured(
            COCAT_SMTP_PORT="2525",
            COCAT_SMTP_USERNAME="cocat",
            COCAT_SMTP_PASSWORD="s3cret",
            COCAT_SMTP_STARTTLS="off",
        )
    )

    assert mail == Mail(
        host="smtp.example.org",
        sender="cocat@example.org",
        port=2525,
        username="cocat",
        password="s3cret",
        starttls=False,
    )


def test_the_defaults_are_the_usual_submission_port_with_starttls() -> None:
    mail = Mail.from_env(configured())

    assert mail == Mail(host="smtp.example.org", sender="cocat@example.org")


def test_a_port_that_is_not_a_number_is_refused() -> None:
    with pytest.raises(ValueError, match="COCAT_SMTP_PORT"):
        Mail.from_env(configured(COCAT_SMTP_PORT="smtp"))


@pytest.mark.parametrize("word", ["1", "true", "YES", "on", " on "])
def test_the_truthy_starttls_words(word: str) -> None:
    mail = Mail.from_env(configured(COCAT_SMTP_STARTTLS=word))

    assert mail is not None
    assert mail.starttls


@pytest.mark.parametrize("word", ["0", "false", "No", "off"])
def test_the_falsey_starttls_words(word: str) -> None:
    mail = Mail.from_env(configured(COCAT_SMTP_STARTTLS=word))

    assert mail is not None
    assert not mail.starttls


def test_a_starttls_word_that_is_neither_is_refused() -> None:
    with pytest.raises(ValueError, match="COCAT_SMTP_STARTTLS"):
        Mail.from_env(configured(COCAT_SMTP_STARTTLS="maybe"))


def test_a_username_without_a_password_is_refused() -> None:
    with pytest.raises(ValueError, match="COCAT_SMTP_PASSWORD"):
        Mail.from_env(configured(COCAT_SMTP_USERNAME="cocat"))


def test_the_repr_hides_the_password() -> None:
    mail = Mail("smtp.example.org", "cocat@example.org", password="s3cret")

    assert "s3cret" not in repr(mail)


def test_placeholder_addresses_are_recognised() -> None:
    assert is_placeholder(f"0000-0002-1825-0097@orcid@{PLACEHOLDER_DOMAIN}")
    assert is_placeholder(f"someone@{PLACEHOLDER_DOMAIN.upper()}")
    assert not is_placeholder("someone@example.org")


async def test_send_reaches_the_relay(smtp: type[FakeSMTP]) -> None:
    mail = Mail(
        "smtp.example.org",
        "cocat@example.org",
        port=2525,
        username="cocat",
        password="s3cret",
    )

    await mail.send("guest@example.org", "Hello", "Body")

    [connection] = smtp.instances
    assert (connection.host, connection.port, connection.timeout) == (
        "smtp.example.org",
        2525,
        10,
    )
    assert connection.started_tls
    assert connection.logged_in == ("cocat", "s3cret")
    [message] = connection.messages
    assert message["To"] == "guest@example.org"
    assert message["From"] == "cocat@example.org"
    assert message["Subject"] == "Hello"
    assert message.get_content().strip() == "Body"


async def test_send_skips_starttls_and_login_when_not_configured(
    smtp: type[FakeSMTP],
) -> None:
    mail = Mail("smtp.example.org", "cocat@example.org", starttls=False)

    await mail.send("guest@example.org", "Hello", "Body")

    [connection] = smtp.instances
    assert not connection.started_tls
    assert connection.logged_in is None


async def test_send_refuses_a_placeholder_address(smtp: type[FakeSMTP]) -> None:
    mail = Mail("smtp.example.org", "cocat@example.org")

    with pytest.raises(MailError, match="placeholder"):
        await mail.send(f"someone@{PLACEHOLDER_DOMAIN}", "Hello", "Body")

    assert smtp.instances == []


@pytest.mark.parametrize(
    "failure", [OSError("unreachable"), smtplib.SMTPException("refused")]
)
async def test_send_reports_a_relay_that_fails(
    smtp: type[FakeSMTP], monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    def fail(self: FakeSMTP, message: Any) -> None:
        raise failure

    monkeypatch.setattr(FakeSMTP, "send_message", fail)
    mail = Mail("smtp.example.org", "cocat@example.org")

    with pytest.raises(MailError, match="smtp.example.org"):
        await mail.send("guest@example.org", "Hello", "Body")


def test_the_verification_mail_names_the_link_and_the_expiry() -> None:
    subject, body = verification_mail("https://cocat.example.org/ui/verify#tok")

    assert "confirm" in subject.lower()
    assert "https://cocat.example.org/ui/verify#tok" in body
    assert "hour" in body


def test_the_reset_mail_names_the_link_and_the_expiry() -> None:
    subject, body = reset_mail("https://cocat.example.org/ui/reset-password#tok")

    assert "password" in subject.lower()
    assert "https://cocat.example.org/ui/reset-password#tok" in body
    assert "hour" in body


def test_the_invitation_mail_names_the_room_and_the_inviter() -> None:
    subject, body = invitation_mail(
        "Lab", "owner@example.org", "https://cocat.example.org/ui/invitation#tok"
    )

    assert "Lab" in subject
    assert "owner@example.org" in body
    assert "https://cocat.example.org/ui/invitation#tok" in body
    assert "14 days" in body
