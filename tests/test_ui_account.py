import pytest
from conftest import UI
from utils import click, fill, set_active, should_notify

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    # aiosqlite only runs on asyncio
    return "asyncio"


async def sign_in(ui: UI) -> None:
    await ui.log_in()
    await ui.user.open("/ui/account")
    await ui.user.should_see(marker="change-password", retries=30)


async def password_works(ui: UI, password: str) -> bool:
    response = await ui.user.http_client.post(
        "/ui/login",
        data={"username": ui.email, "password": password},
        follow_redirects=False,
    )
    return "error=" not in str(response.headers["location"])


def change(ui: UI, current: str, new: str, repeat: str) -> None:
    fill(ui.user, "current-password", current)
    fill(ui.user, "new-password", new)
    fill(ui.user, "repeat-password", repeat)
    click(ui.user, "change-password")


async def test_requires_login(ui: UI) -> None:
    await ui.user.open("/ui/account")

    await ui.user.should_see("Sign in")


async def test_shows_the_signed_in_email(ui: UI) -> None:
    await sign_in(ui)

    await ui.user.should_see(marker="account-email", content=ui.email)


async def test_header_offers_account(ui: UI) -> None:
    await ui.log_in()
    await ui.user.open("/ui/rooms")
    await ui.user.should_see(marker="nav-account", retries=30)

    click(ui.user, "nav-account")

    await ui.user.should_see(marker="change-password", retries=30)


async def test_change_password(ui: UI) -> None:
    await sign_in(ui)

    change(ui, ui.password, "new-password", "new-password")

    await should_notify(ui.user, "Password changed")
    assert await password_works(ui, "new-password")
    assert not await password_works(ui, ui.password)


async def test_wrong_current_password_changes_nothing(ui: UI) -> None:
    await sign_in(ui)

    change(ui, "not-my-password", "new-password", "new-password")

    await should_notify(ui.user, "Wrong password")
    assert await password_works(ui, ui.password)
    assert not await password_works(ui, "new-password")


async def test_mismatched_repeat_is_refused(ui: UI) -> None:
    await sign_in(ui)

    change(ui, ui.password, "new-password", "other-password")

    await should_notify(ui.user, "The new passwords differ")


async def test_empty_new_password_is_refused(ui: UI) -> None:
    await sign_in(ui)

    change(ui, ui.password, "", "")

    await should_notify(ui.user, "Enter a new password")


async def test_change_is_refused_after_deactivation(ui: UI) -> None:
    await sign_in(ui)
    await set_active(ui.db_path, ui.email, False)

    change(ui, ui.password, "new-password", "new-password")

    await should_notify(ui.user, "Please sign in again")
    await set_active(ui.db_path, ui.email, True)
    assert await password_works(ui, ui.password)
