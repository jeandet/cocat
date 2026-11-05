from urllib.parse import parse_qs, urlsplit

import pytest
from conftest import UI
from httpx_oauth.exceptions import GetIdEmailError
from httpx_oauth.oauth2 import GetAccessTokenError
from utils import ensure_user, set_active

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    """aiosqlite and NiceGUI only run on asyncio, so these tests cannot use trio."""
    return "asyncio"


async def start_login(ui: UI) -> str:
    """Begin the dance and come back with the state the provider was handed."""
    response = await ui.user.http_client.get(
        "/ui/oauth/orcid/login", follow_redirects=False
    )
    assert response.status_code == 307
    query = parse_qs(urlsplit(str(response.headers["location"])).query)
    return query["state"][0]


async def finish_login(ui: UI, state: str, code: str = "code"):
    return await ui.user.http_client.get(
        f"/ui/oauth/orcid/callback?code={code}&state={state}", follow_redirects=False
    )


async def test_login_redirects_to_the_provider(ui_oauth: UI) -> None:
    response = await ui_oauth.user.http_client.get(
        "/ui/oauth/orcid/login", follow_redirects=False
    )

    location = str(response.headers["location"])
    assert location.startswith("https://provider.example.org/authorize?")
    assert "scope=openid" in location
    assert ui_oauth.user.http_client.cookies.get("fastapiusersoauthcsrf") is not None


async def test_the_login_page_lists_the_provider(ui_oauth: UI) -> None:
    await ui_oauth.user.open("/ui/login")

    await ui_oauth.user.should_see(marker="oauth-orcid", retries=30)


async def test_a_good_callback_signs_in(ui_oauth: UI) -> None:
    state = await start_login(ui_oauth)

    response = await finish_login(ui_oauth, state)

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/rooms"
    assert ui_oauth.user.http_client.cookies.get("fastapiusersauth") is not None
    await ui_oauth.user.open("/ui/rooms")
    await ui_oauth.user.should_see(marker="nav-rooms", retries=30)


async def test_a_forged_state_is_refused(ui_oauth: UI) -> None:
    response = await finish_login(ui_oauth, "forged")

    assert response.status_code == 400


async def test_a_missing_cookie_is_refused(ui_oauth: UI) -> None:
    state = await start_login(ui_oauth)
    ui_oauth.user.http_client.cookies.delete("fastapiusersoauthcsrf")

    response = await finish_login(ui_oauth, state)

    assert response.status_code == 400


async def test_a_missing_code_is_refused(ui_oauth: UI) -> None:
    state = await start_login(ui_oauth)

    response = await ui_oauth.user.http_client.get(
        f"/ui/oauth/orcid/callback?state={state}", follow_redirects=False
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login?error=provider"


async def test_a_token_error_is_refused(ui_oauth: UI) -> None:
    state = await start_login(ui_oauth)
    assert ui_oauth.oauth_client is not None
    ui_oauth.oauth_client.failure = GetAccessTokenError("no token")

    response = await finish_login(ui_oauth, state)

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login?error=provider"


async def test_a_provider_without_an_email_asks_for_one(
    ui_oauth_placeholder: UI,
) -> None:
    state = await start_login(ui_oauth_placeholder)

    response = await finish_login(ui_oauth_placeholder, state)

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/confirm-email"


async def test_a_deactivated_account_is_refused(ui_oauth: UI) -> None:
    state = await start_login(ui_oauth)
    await finish_login(ui_oauth, state)
    await set_active(ui_oauth.db_path, "guest@example.org", False)
    cookie = ui_oauth.user.http_client.cookies.get("fastapiusersauth")

    response = await finish_login(ui_oauth, await start_login(ui_oauth))

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login?error=credentials"
    assert cookie is not None


async def test_the_api_offers_an_authorization_url(ui_oauth: UI) -> None:
    response = await ui_oauth.user.http_client.get(
        "/auth/orcid/authorize", follow_redirects=False
    )

    assert response.status_code == 200
    assert response.json()["authorization_url"].startswith(
        "https://provider.example.org/authorize"
    )


async def test_a_taken_address_is_reported(ui_oauth: UI) -> None:
    await ensure_user(ui_oauth.db_path, "guest@example.org")
    state = await start_login(ui_oauth)

    response = await finish_login(ui_oauth, state)

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login?error=exists"


async def test_a_cross_site_login_is_refused(ui_oauth: UI) -> None:
    response = await ui_oauth.user.http_client.get(
        "/ui/oauth/orcid/login",
        headers={"sec-fetch-site": "cross-site"},
        follow_redirects=False,
    )

    assert response.status_code == 403


async def test_a_profile_error_returns_to_the_login_page(ui_oauth: UI) -> None:
    state = await start_login(ui_oauth)
    assert ui_oauth.oauth_client is not None
    ui_oauth.oauth_client.failure = GetIdEmailError("no profile")

    response = await finish_login(ui_oauth, state)

    assert response.status_code == 303
    assert response.headers["location"] == "/ui/login?error=provider"


async def test_the_login_route_is_rate_limited(ui_oauth: UI) -> None:
    for _ in range(10):
        await ui_oauth.user.http_client.get(
            "/ui/oauth/orcid/login", follow_redirects=False
        )

    response = await ui_oauth.user.http_client.get(
        "/ui/oauth/orcid/login", follow_redirects=False
    )

    assert response.status_code == 429
