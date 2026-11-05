import pytest
from conftest import UI
from sqlalchemy.ext.asyncio import AsyncSession
from utils import (
    click,
    ensure_user,
    fill,
    in_session,
    set_active,
    should_notify,
    user_by_email,
)

from cocat.app.oauth import placeholder_email

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    """aiosqlite and NiceGUI only run on asyncio, so these tests cannot use trio."""
    return "asyncio"


async def make_placeholder(ui: UI) -> None:
    async def replace(session: AsyncSession) -> None:
        account = await user_by_email(session, ui.email)
        account.email = placeholder_email("orcid", "orcid-1234")
        await session.commit()

    await in_session(ui.db_path, replace)


async def open_page(ui: UI) -> None:
    await ui.log_in()
    await make_placeholder(ui)
    await ui.user.open("/ui/confirm-email")
    await ui.user.should_see(marker="confirm-email-send", retries=30)


async def address_of(ui: UI, address: str) -> bool:
    async def read(session: AsyncSession) -> None:
        await user_by_email(session, address)

    try:
        await in_session(ui.db_path, read)
    except AssertionError:
        return False
    return True


async def test_a_new_address_is_confirmed_without_a_mailer(ui: UI) -> None:
    await open_page(ui)

    fill(ui.user, "confirm-email", "fresh@example.org")
    click(ui.user, "confirm-email-send")

    await should_notify(ui.user, "Your address is confirmed.")
    assert await address_of(ui, "fresh@example.org")


async def test_with_a_mailer_the_address_is_asked_to_verify(ui_mail: UI) -> None:
    await open_page(ui_mail)

    fill(ui_mail.user, "confirm-email", "fresh@example.org")
    click(ui_mail.user, "confirm-email-send")

    await should_notify(ui_mail.user, "Check your inbox to verify fresh@example.org.")
    assert ui_mail.mail is not None
    assert len(ui_mail.mail.mails_to("fresh@example.org")) == 1


async def test_an_address_owned_by_somebody_else_is_reported(ui: UI) -> None:
    await ensure_user(ui.db_path, "taken@example.org")
    await open_page(ui)

    fill(ui.user, "confirm-email", "taken@example.org")
    click(ui.user, "confirm-email-send")

    await should_notify(
        ui.user,
        "That address belongs to an existing account: sign in with it and "
        "reset its password if you do not have one",
    )


async def test_an_empty_address_is_refused(ui: UI) -> None:
    await open_page(ui)

    fill(ui.user, "confirm-email", " ")
    click(ui.user, "confirm-email-send")

    await should_notify(ui.user, "Enter your email address")
    assert await address_of(ui, "fresh@example.org") is False


async def test_requires_login(ui: UI) -> None:
    await ui.user.open("/ui/confirm-email")

    await ui.user.should_see("Sign in")


async def test_a_placeholder_address_is_refused(ui: UI) -> None:
    await open_page(ui)

    fill(ui.user, "confirm-email", placeholder_email("orcid", "orcid-1234"))
    click(ui.user, "confirm-email-send")

    await should_notify(ui.user, "Enter your email address")


async def test_a_lost_session_is_reported(ui: UI) -> None:
    await open_page(ui)

    await set_active(ui.db_path, placeholder_email("orcid", "orcid-1234"), False)

    fill(ui.user, "confirm-email", "fresh@example.org")
    click(ui.user, "confirm-email-send")

    await should_notify(ui.user, "Please sign in again")


async def test_the_account_page_links_to_it_for_a_placeholder(ui: UI) -> None:
    await ui.log_in()
    await make_placeholder(ui)

    await ui.user.open("/ui/account")
    await ui.user.should_see(marker="nav-confirm-email", retries=30)


async def test_a_proven_address_is_sent_to_the_account_page(ui: UI) -> None:
    await ui.log_in()

    await ui.user.open("/ui/confirm-email")

    await ui.user.should_see(marker="change-password", retries=30)


async def test_a_malformed_address_is_refused(ui: UI) -> None:
    await open_page(ui)

    fill(ui.user, "confirm-email", "not an address")
    click(ui.user, "confirm-email-send")

    await should_notify(ui.user, "That is not an email address")
