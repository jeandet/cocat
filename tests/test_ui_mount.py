import pytest
from conftest import UI

pytestmark = pytest.mark.anyio


async def test_login_page_is_served(ui: UI) -> None:
    await ui.user.open("/ui/login")
    await ui.user.should_see("Sign in")


async def test_root_redirects_to_rooms(ui: UI) -> None:
    response = await ui.user.http_client.get("/ui/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/ui/rooms"


async def test_login_page_redirects_a_signed_in_visitor(ui: UI) -> None:
    await ui.log_in()

    response = await ui.user.http_client.get("/ui/login", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/ui/rooms"


async def test_login_page_shows_an_error(ui: UI) -> None:
    await ui.user.open("/ui/login?error=credentials")
    await ui.user.should_see("Wrong email or password.")
