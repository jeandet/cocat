import re

import httpx
import pytest
from conftest import UI
from nicegui.elements.html import Html
from utils import click, fill, should_notify

from cocat.app.mail import MailError
from cocat.ui.token import token_from_text

pytestmark = pytest.mark.anyio

FORGOT = "/ui/forgot-password"
NEW = "brand-new-password"


@pytest.fixture()
def anyio_backend() -> str:
    """aiosqlite and NiceGUI only run on asyncio, so these tests cannot use trio."""
    return "asyncio"


async def forgot(ui: UI, email: str | None = None) -> httpx.Response:
    return await ui.user.http_client.post(
        FORGOT,
        data={"email": email if email is not None else ui.email},
        follow_redirects=False,
    )


def form_html(ui: UI, marker: str) -> str:
    element = next(iter(ui.user.find(marker=marker).elements))
    assert isinstance(element, Html)
    return element.content


def location(response: httpx.Response) -> str:
    return str(response.headers["location"])


def answer_fragment(ui: UI, token: str) -> None:
    ui.user.javascript_rules[re.compile(r"window\.location\.hash")] = lambda _: token


async def can_sign_in(ui: UI, password: str) -> bool:
    response = await ui.user.http_client.post(
        "/ui/login",
        data={"username": ui.email, "password": password},
        follow_redirects=False,
    )
    return "error=" not in str(response.headers["location"])


async def open_reset(ui: UI, token: str) -> None:
    answer_fragment(ui, token)
    await ui.user.open("/ui/reset-password")
    await ui.user.should_see(marker="reset-submit", retries=30)


def submit(ui: UI, password: str = NEW, repeat: str | None = None) -> None:
    fill(ui.user, "new-password", password)
    fill(ui.user, "repeat-password", password if repeat is None else repeat)
    click(ui.user, "reset-submit")


async def test_the_page_renders_the_form(ui: UI) -> None:
    await ui.user.open(FORGOT)

    await ui.user.should_see(marker="forgot-form", retries=30)
    form = form_html(ui, "forgot-form")
    assert 'action="/ui/forgot-password"' in form
    assert 'name="email"' in form
    assert "forgot-submit" in form


async def test_an_unknown_address_is_told_nothing(ui_mail: UI) -> None:
    response = await forgot(ui_mail, "nobody@example.org")

    assert location(response) == f"{FORGOT}?sent=1"
    assert ui_mail.mail is not None
    assert ui_mail.mail.sent == []


async def test_a_known_address_gets_a_link(ui_mail: UI) -> None:
    response = await forgot(ui_mail)

    assert location(response) == f"{FORGOT}?sent=1"
    assert ui_mail.mail is not None
    [(_to, _subject, body)] = ui_mail.mail.mails_to(ui_mail.email)
    assert "/ui/reset-password#" in body


async def test_a_mailer_that_fails_is_swallowed(
    ui_mail: UI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(*args: object) -> None:
        raise MailError("relay down")

    assert ui_mail.mail is not None
    monkeypatch.setattr(ui_mail.mail, "send", refuse)

    response = await forgot(ui_mail)

    assert location(response) == f"{FORGOT}?sent=1"


async def test_the_whole_flow(ui_mail: UI) -> None:
    await forgot(ui_mail)
    assert ui_mail.mail is not None
    [(_to, _subject, body)] = ui_mail.mail.mails_to(ui_mail.email)
    token = token_from_text(body)
    await open_reset(ui_mail, token)

    submit(ui_mail)

    await should_notify(ui_mail.user, "Password changed")
    assert await can_sign_in(ui_mail, NEW)
    assert not await can_sign_in(ui_mail, ui_mail.password)


async def test_a_bad_token_is_reported(ui: UI) -> None:
    await open_reset(ui, "not-a-token")

    submit(ui)

    await should_notify(ui.user, "This reset link is not valid anymore.")


async def test_a_mismatched_repeat_is_refused(ui_mail: UI) -> None:
    await open_reset(ui_mail, "whatever")

    submit(ui_mail, NEW, "something-else")

    await should_notify(ui_mail.user, "The new passwords differ")
    assert await can_sign_in(ui_mail, ui_mail.password)


async def test_a_flooded_limiter_is_refused(ui: UI) -> None:
    for _ in range(10):
        await ui.user.http_client.post(
            "/ui/login", data={"username": "x@example.org", "password": "wrong"}
        )

    response = await forgot(ui)

    assert location(response) == f"{FORGOT}?error=too_many"


async def test_an_empty_password_is_refused(ui_mail: UI) -> None:
    await open_reset(ui_mail, "whatever")

    submit(ui_mail, "")

    await should_notify(ui_mail.user, "Enter a new password")
    assert await can_sign_in(ui_mail, ui_mail.password)
