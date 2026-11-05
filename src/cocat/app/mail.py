"""Outgoing email, configured from the environment.

Without `COCAT_SMTP_HOST` there is no sender at all: the server still runs, no
verification is asked for, and the web UI shows the links it cannot mail.
"""

import os
import smtplib
from collections.abc import Mapping
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Protocol

import anyio.to_thread

PLACEHOLDER_DOMAIN = "placeholder.cocat.org"

TIMEOUT_SECONDS = 10

TRUTHY = {"1", "true", "yes", "on"}
FALSEY = {"0", "false", "no", "off"}


class MailError(Exception):
    """A message could not be built or handed to the relay."""


def is_placeholder(email: str) -> bool:
    """Whether an address is the stand-in we keep for accounts without one.

    Providers such as ORCID may not release an address, and the accounts table
    needs something unique in `email`; `placeholder.cocat.org` is reserved for
    that stand-in, and nothing is ever mailed to it.
    """
    _, _, domain = email.rpartition("@")
    domain = domain.lower()
    # One subdomain per provider, so two providers cannot collide on the same id.
    return domain == PLACEHOLDER_DOMAIN or domain.endswith("." + PLACEHOLDER_DOMAIN)


class Mailer(Protocol):
    async def send(self, to: str, subject: str, body: str) -> None: ...


@dataclass(frozen=True)
class Mail:
    """A submission server.

    Implicit TLS on port 465 is not supported: plain submission with STARTTLS
    (port 587) covers the relays we document.
    """

    host: str
    sender: str
    port: int = 587
    username: str | None = None
    password: str | None = field(default=None, repr=False)
    starttls: bool = True

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "Mail | None":
        host = environ.get("COCAT_SMTP_HOST", "").strip()
        if not host:
            return None
        sender = environ.get("COCAT_MAIL_FROM", "").strip()
        if not sender:
            raise ValueError("COCAT_MAIL_FROM is required when COCAT_SMTP_HOST is set")
        return cls(
            host=host,
            sender=sender,
            port=_port(environ.get("COCAT_SMTP_PORT", "587")),
            username=_username(environ),
            password=environ.get("COCAT_SMTP_PASSWORD") or None,
            starttls=_starttls(environ.get("COCAT_SMTP_STARTTLS", "on")),
        )

    async def send(self, to: str, subject: str, body: str) -> None:
        if is_placeholder(to):
            raise MailError(f"{to} is a placeholder address and receives no mail")
        try:
            message = EmailMessage()
            message["To"] = to
            message["From"] = self.sender
            message["Subject"] = subject
            message.set_content(body)
            await anyio.to_thread.run_sync(self._deliver, message)
        except (OSError, smtplib.SMTPException, ValueError) as error:
            # A header with a newline raises ValueError before the relay is
            # reached; callers get the same MailError as a refusal.
            raise MailError(f"{self.host} refused the message: {error}") from error

    def _deliver(self, message: EmailMessage) -> None:
        smtp = smtplib.SMTP(self.host, self.port, timeout=TIMEOUT_SECONDS)
        try:
            if self.starttls:
                smtp.starttls()
            if self.username is not None and self.password is not None:
                smtp.login(self.username, self.password)
            smtp.send_message(message)
        finally:
            smtp.close()


def _port(value: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"COCAT_SMTP_PORT must be a number, not {value!r}") from None


def _starttls(value: str) -> bool:
    word = value.strip().lower()
    if word in TRUTHY:
        return True
    if word in FALSEY:
        return False
    raise ValueError(
        f"COCAT_SMTP_STARTTLS must be one of {sorted(TRUTHY | FALSEY)}, not {value!r}"
    )


def _username(environ: Mapping[str, str]) -> str | None:
    username = environ.get("COCAT_SMTP_USERNAME") or None
    if username is not None and not environ.get("COCAT_SMTP_PASSWORD"):
        raise ValueError("COCAT_SMTP_PASSWORD is required with COCAT_SMTP_USERNAME")
    return username


def verification_mail(link: str) -> tuple[str, str]:
    return (
        "Confirm your cocat address",
        (
            f"Open this link to confirm your address:\n\n{link}\n\n"
            "The link is valid for an hour. If you did not sign up, ignore this message.\n"
        ),
    )


def reset_mail(link: str) -> tuple[str, str]:
    return (
        "Reset your cocat password",
        (
            f"Open this link to choose a new password:\n\n{link}\n\n"
            "The link is valid for an hour. If you did not ask for it, ignore this message.\n"
        ),
    )


def invitation_mail(room_name: str, inviter: str, link: str) -> tuple[str, str]:
    return (
        f"{inviter} invites you to the room {room_name}",
        (
            f'{inviter} invites you to join the room "{room_name}":\n\n{link}\n\n'
            "The invitation is valid for 14 days.\n"
        ),
    )
