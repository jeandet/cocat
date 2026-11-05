import re

import pytest
from conftest import UI

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    """aiosqlite and NiceGUI only run on asyncio, so these tests cannot use trio."""
    return "asyncio"


NEW = "verify@example.org"
PASSWORD = "pwd123"


def answer_fragment(ui: UI, token: str | None) -> None:
    ui.user.javascript_rules[re.compile(r"window\.location\.hash")] = lambda _: token


async def registered(ui: UI, email: str = NEW) -> str:
    """Register an account through the API and return the token it was mailed."""
    response = await ui.user.http_client.post(
        "/auth/register", json={"email": email, "password": PASSWORD}
    )
    assert response.status_code == 201
    assert ui.mail is not None
    [(_to, _subject, body)] = ui.mail.mails_to(email)
    return body.split("/ui/verify#", 1)[1].split()[0]


async def can_sign_in(ui: UI, email: str = NEW) -> bool:
    response = await ui.user.http_client.post(
        "/ui/login",
        data={"username": email, "password": PASSWORD},
        follow_redirects=False,
    )
    return "error=" not in str(response.headers["location"])


async def test_a_valid_token_verifies_the_account(ui_mail: UI) -> None:
    ui = ui_mail
    token = await registered(ui)
    assert not await can_sign_in(ui)

    answer_fragment(ui, token)
    await ui.user.open("/ui/verify")

    await ui.user.should_see("Your address is verified", retries=30)
    assert await can_sign_in(ui)


async def test_an_already_verified_address_is_reported(ui_mail: UI) -> None:
    ui = ui_mail
    token = await registered(ui)
    answer_fragment(ui, token)
    await ui.user.open("/ui/verify")
    await ui.user.should_see("Your address is verified", retries=30)

    answer_fragment(ui, token)
    await ui.user.open("/ui/verify")

    await ui.user.should_see("Your address is already verified", retries=30)


async def test_a_bad_token_is_reported(ui: UI) -> None:
    answer_fragment(ui, "not-a-token")

    await ui.user.open("/ui/verify")

    await ui.user.should_see("This verification link is not valid anymore")


async def test_without_a_token_the_page_explains_what_to_do(ui: UI) -> None:
    answer_fragment(ui, None)

    await ui.user.open("/ui/verify")

    await ui.user.should_see("Open the link from your inbox")


async def test_the_token_never_reaches_a_notification(ui_mail: UI) -> None:
    ui = ui_mail
    token = await registered(ui)
    answer_fragment(ui, token)

    await ui.user.open("/ui/verify")
    await ui.user.should_see("Your address is verified", retries=30)

    assert all(token not in message for message in ui.user.notify.messages)
