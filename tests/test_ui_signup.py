import httpx
import pytest
from conftest import UI, AppClient
from nicegui.elements.html import Html
from sqlalchemy.ext.asyncio import AsyncSession
from utils import in_session, user_by_email

from cocat.app.mail import MailError

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    """aiosqlite and NiceGUI only run on asyncio, so these tests cannot use trio."""
    return "asyncio"


NEW = "signup@example.org"
PASSWORD = "pwd123"


def form_html(ui: UI, marker: str) -> str:
    element = next(iter(ui.user.find(marker=marker).elements))
    assert isinstance(element, Html)
    return element.content


async def sign_up(ui: UI, email: str = NEW, password: str = PASSWORD) -> httpx.Response:
    return await ui.user.http_client.post(
        "/ui/signup",
        data={"email": email, "password": password},
        follow_redirects=False,
    )


def location(response: httpx.Response) -> str:
    return str(response.headers["location"])


async def exists(ui: UI, email: str) -> bool:
    async def find(session: AsyncSession) -> None:
        await user_by_email(session, email)

    try:
        await in_session(ui.db_path, find)
    except AssertionError:
        return False
    return True


async def test_the_page_renders_the_form(ui: UI) -> None:
    await ui.user.open("/ui/signup")

    html = form_html(ui, "signup-form")
    assert 'action="/ui/signup"' in html
    assert 'name="email"' in html
    assert 'name="password"' in html
    assert "signup-submit" in html


async def test_the_login_page_offers_sign_up_and_reset(ui: UI) -> None:
    await ui.user.open("/ui/login")

    html = form_html(ui, "login-form")
    assert 'href="/ui/signup"' in html
    assert 'href="/ui/forgot-password"' in html
    assert "administrator" not in html


async def test_sign_up_with_a_mailer_asks_for_verification(ui_mail: UI) -> None:
    response = await sign_up(ui_mail)

    assert location(response) == "/ui/login?created=1"
    assert ui_mail.user.http_client.cookies.get("fastapiusersauth") is None
    [(to, _subject, body)] = ui_mail.mail.mails_to(NEW)  # type: ignore[union-attr]
    assert to == NEW
    assert "/ui/verify#" in body


async def test_the_created_message_reaches_the_login_page(ui_mail: UI) -> None:
    await sign_up(ui_mail)

    await ui_mail.user.open("/ui/login?created=1")

    await ui_mail.user.should_see("Account created")


async def test_sign_up_without_a_mailer_lands_in_the_rooms(ui: UI) -> None:
    response = await sign_up(ui)

    assert location(response) == "/ui/rooms"
    assert ui.user.http_client.cookies.get("fastapiusersauth") is not None
    await ui.user.open("/ui/rooms")
    await ui.user.should_see(marker="nav-rooms", retries=30)


async def test_a_duplicate_address_is_refused(ui: UI) -> None:
    await sign_up(ui)

    response = await sign_up(ui)

    assert location(response) == "/ui/signup?error=exists"


async def test_an_address_that_is_not_an_email_is_refused(ui: UI) -> None:
    response = await sign_up(ui, email="not-an-address")

    assert location(response) == "/ui/signup?error=email"
    assert not await exists(ui, "not-an-address")


async def test_a_flooded_limiter_is_refused(ui: UI) -> None:
    for _ in range(10):
        await ui.user.http_client.post(
            "/ui/login", data={"username": NEW, "password": "wrong"}
        )

    response = await sign_up(ui)

    assert location(response) == "/ui/signup?error=too_many"


async def test_a_mailer_that_fails_still_creates_the_account(
    ui_mail: UI, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(*args: object, **kwargs: object) -> None:
        raise MailError("the relay is down")

    assert ui_mail.mail is not None
    monkeypatch.setattr(ui_mail.mail, "send", refuse)

    response = await sign_up(ui_mail)

    assert location(response) == "/ui/login?error=mail"
    assert await exists(ui_mail, NEW)


def test_registration_is_public_and_rate_limited(app_client: AppClient) -> None:
    for index in range(10):
        response = app_client.client.post(
            "/auth/register",
            json={"email": f"new{index}@example.org", "password": "pwd"},
        )
        assert response.status_code == 201

    response = app_client.client.post(
        "/auth/register", json={"email": "late@example.org", "password": "pwd"}
    )

    assert response.status_code == 429
