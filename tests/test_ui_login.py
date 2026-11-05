from collections.abc import AsyncIterator

import httpx
import pytest
from conftest import UI
from utils import COOKIE_NAME, set_active, set_verified

from cocat.ui.auth import login_form, logout_form

pytestmark = pytest.mark.anyio

SIGN_UP = "/ui/signup"
RESET = "/ui/forgot-password"


async def post_login(
    ui: UI,
    password: str = "pwd",
    next_path: str | None = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    params = {} if next_path is None else {"next": next_path}
    return await ui.user.http_client.post(
        "/ui/login",
        data={"username": ui.email, "password": password},
        params=params,
        headers=headers,
        follow_redirects=False,
    )


@pytest.fixture()
async def logged_in(ui: UI) -> AsyncIterator[UI]:
    await ui.log_in()
    yield ui


def test_login_form_posts_the_password() -> None:
    form = login_form("/ui/login?next=%2Fui%2Frooms", None, SIGN_UP, RESET)

    assert 'method="post"' in form
    assert 'action="/ui/login?next=%2Fui%2Frooms"' in form
    assert 'name="username"' in form
    assert 'name="password"' in form
    assert 'type="password"' in form
    assert "Log in" in form
    assert 'href="/ui/signup"' in form
    assert 'href="/ui/forgot-password"' in form
    assert "administrator" not in form


def test_login_form_explains_the_failure() -> None:
    assert "Wrong email or password." in login_form(
        "/ui/login", "credentials", SIGN_UP, RESET
    )
    assert "Too many" in login_form("/ui/login", "too_many", SIGN_UP, RESET)
    assert "Wrong email or password." not in login_form(
        "/ui/login", None, SIGN_UP, RESET
    )


def test_login_form_escapes_the_action() -> None:
    form = login_form('/ui/login?a=<script>&b="', None, SIGN_UP, RESET)

    assert "<script>" not in form
    assert "&lt;script&gt;" in form


def test_logout_form_posts() -> None:
    form = logout_form("/ui/logout?a=<script>")

    assert 'method="post"' in form
    assert 'action="/ui/logout?a=&lt;script&gt;"' in form
    assert "Log out" in form


async def test_login_sets_the_cookie_and_redirects(ui: UI) -> None:
    response = await ui.log_in()

    assert response.headers["location"] == "/ui/rooms"
    assert COOKIE_NAME in ui.user.http_client.cookies


async def test_login_without_next_goes_to_the_rooms_page(ui: UI) -> None:
    response = await post_login(ui)

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/rooms"


async def test_login_rejects_a_bad_password(ui: UI) -> None:
    response = await post_login(ui, password="wrong")

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login?error=credentials"
    assert COOKIE_NAME not in response.cookies


async def test_login_rejects_an_inactive_user(ui: UI) -> None:
    await set_active(ui.db_path, ui.email, False)

    response = await post_login(ui)

    assert response.headers["location"] == "/ui/login?error=credentials"
    assert COOKIE_NAME not in response.cookies


async def test_inactive_user_page_is_not_signed_in(ui: UI) -> None:
    await ui.log_in()
    await set_active(ui.db_path, ui.email, False)

    await ui.user.open("/ui/login")

    await ui.user.should_see("Log in")


async def test_login_honours_a_local_next(ui: UI) -> None:
    response = await post_login(ui, next_path="/ui/account")

    assert response.headers["location"] == "/ui/account"


async def test_login_keeps_a_query(ui: UI) -> None:
    response = await post_login(ui, next_path="/ui/rooms?x=1")

    assert response.headers["location"] == "/ui/rooms?x=1"


@pytest.mark.parametrize(
    "next_path",
    [
        "//evil.example",
        "///evil.example",
        "////evil.example",
        "https://evil.example/",
        "/\\evil.example",
        "evil.example",
    ],
)
async def test_login_refuses_a_foreign_next(ui: UI, next_path: str) -> None:
    response = await post_login(ui, next_path=next_path)

    assert response.headers["location"] == "/ui/rooms"


async def test_login_is_rate_limited(ui: UI) -> None:
    responses = [await post_login(ui, password="wrong") for _ in range(11)]

    assert [response.headers["location"] for response in responses[:10]] == [
        "/ui/login?error=credentials"
    ] * 10
    assert responses[10].status_code == 303
    assert responses[10].headers["location"] == "/ui/login?error=too_many"


async def test_cross_site_post_is_refused(ui: UI) -> None:
    foreign_origin = await post_login(ui, headers={"Origin": "https://evil.example"})
    cross_site = await post_login(ui, headers={"Sec-Fetch-Site": "cross-site"})
    same_site = await post_login(ui, headers={"Sec-Fetch-Site": "same-site"})

    assert foreign_origin.status_code == 403
    assert cross_site.status_code == 403
    assert COOKIE_NAME not in foreign_origin.cookies
    assert same_site.status_code == 303


async def test_logout_clears_the_cookie(logged_in: UI) -> None:
    response = await logged_in.user.http_client.post(
        "/ui/logout", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login"
    assert COOKIE_NAME not in logged_in.user.http_client.cookies


async def test_logout_without_a_cookie(ui: UI) -> None:
    response = await ui.user.http_client.post("/ui/logout", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login"


@pytest.fixture()
def anyio_backend() -> str:
    """aiosqlite only runs on asyncio, so these tests cannot use trio."""
    return "asyncio"


async def test_the_login_page_offers_a_resend(ui: UI) -> None:
    await ui.user.open("/ui/login")

    await ui.user.should_see(marker="resend-form", retries=30)


async def test_resending_answers_the_same_for_an_unknown_address(ui: UI) -> None:
    response = await ui.user.http_client.post(
        "/ui/request-verify",
        data={"email": "nobody@example.org"},
        follow_redirects=False,
    )

    assert str(response.headers["location"]) == "/ui/login?resent=1"


async def test_resending_mails_an_unverified_account(ui_mail: UI) -> None:
    await set_verified(ui_mail.db_path, ui_mail.email, False)

    response = await ui_mail.user.http_client.post(
        "/ui/request-verify",
        data={"email": ui_mail.email},
        follow_redirects=False,
    )

    assert str(response.headers["location"]) == "/ui/login?resent=1"
    assert ui_mail.mail is not None
    assert ui_mail.mail.mails_to(ui_mail.email)


async def test_a_flood_of_logins_does_not_block_sign_up(ui: UI) -> None:
    for _ in range(10):
        await ui.user.http_client.post(
            "/ui/login",
            data={"username": "x@example.org", "password": "nope"},
            follow_redirects=False,
        )

    response = await ui.user.http_client.post(
        "/ui/signup",
        data={"email": "fresh@example.org", "password": "pwd123"},
        follow_redirects=False,
    )

    assert "too_many" not in str(response.headers["location"])


async def test_a_flood_of_resends_is_refused(ui: UI) -> None:
    for _ in range(10):
        await ui.user.http_client.post(
            "/ui/request-verify",
            data={"email": "x@example.org"},
            follow_redirects=False,
        )

    response = await ui.user.http_client.post(
        "/ui/request-verify",
        data={"email": "x@example.org"},
        follow_redirects=False,
    )

    assert "error=too_many" in str(response.headers["location"])
