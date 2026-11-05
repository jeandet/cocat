from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from conftest import UI
from nicegui.client import Client
from nicegui.elements.html import Html
from sqlalchemy.ext.asyncio import AsyncSession
from utils import (
    click,
    fill,
    in_session,
    set_active,
    should_notify,
    user_by_email,
)
from utils import (
    rooms_of as _rooms_of,
)

from cocat.app.db import Role
from cocat.app.rooms import add_member, create_room
from cocat.cli import _create_user

pytestmark = pytest.mark.anyio


async def rooms_of(ui: UI) -> dict[str, str]:
    return await _rooms_of(ui.db_path, ui.email)


async def room_named(ui: UI, name: str) -> str:
    rooms = await rooms_of(ui)
    assert name in rooms, sorted(rooms)
    return rooms[name]


async def add_room_owned_elsewhere(ui: UI, name: str) -> str:
    owner_email = f"owner-{uuid4().hex}@foo.com"
    await _create_user(owner_email, "pwd", False, ui.db_path)

    async def add(session: AsyncSession) -> str:
        room = await create_room(
            session, await user_by_email(session, owner_email), name
        )
        member = await user_by_email(session, ui.email)
        await add_member(session, room.id, member.id, Role.editor)
        return room.id

    return await in_session(ui.db_path, add)


def unique(name: str) -> str:
    return f"{name}-{uuid4().hex}"


async def create_named(ui: UI, name: str) -> str:
    fill(ui.user, "new-room-name", name)
    click(ui.user, "create-room")
    await should_notify(ui.user, "Room created")
    await ui.user.should_see(name, retries=10)
    return await room_named(ui, name)


@pytest.fixture()
async def signed_in(ui: UI) -> AsyncIterator[UI]:
    await ui.log_in()
    await ui.user.open("/ui/rooms")
    yield ui


async def test_requires_login(ui: UI) -> None:
    await ui.user.open("/ui/rooms")

    await ui.user.should_see("Sign in")


async def test_signed_out_page_does_not_leak_a_client(ui: UI) -> None:
    for _ in range(3):
        response = await ui.user.http_client.get("/ui/rooms", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"].startswith("/ui/login?next=")

    assert not Client.instances


async def test_redirect_pages_do_not_leak_a_client(ui: UI) -> None:
    for path in ("/ui/", "/ui/"):
        response = await ui.user.http_client.get(path, follow_redirects=False)
        assert response.status_code == 307

    await ui.log_in()
    for _ in range(2):
        response = await ui.user.http_client.get("/ui/login", follow_redirects=False)
        assert response.status_code == 307

    assert not Client.instances


async def test_lists_your_rooms(signed_in: UI) -> None:
    personal = await room_named(signed_in, "Personal")

    await signed_in.user.should_see(marker=f"room-{personal}")
    await signed_in.user.should_see("owner")


async def test_create_room(signed_in: UI) -> None:
    name = unique("lab")

    room_id = await create_named(signed_in, name)

    assert room_id in (await rooms_of(signed_in)).values()
    await signed_in.user.open("/ui/rooms")
    await signed_in.user.should_see(name)


async def test_create_room_needs_a_name(signed_in: UI) -> None:
    click(signed_in.user, "create-room")

    await should_notify(signed_in.user, "Enter a room name")


async def test_rename_room(signed_in: UI) -> None:
    first, second = unique("lab"), unique("room")
    room_id = await create_named(signed_in, first)

    click(signed_in.user, f"rename-{room_id}")
    fill(signed_in.user, "confirm-name", second)
    click(signed_in.user, "confirm")

    await should_notify(signed_in.user, "Room renamed")
    await signed_in.user.should_see(second, retries=10)
    assert second in await rooms_of(signed_in)


async def test_delete_room(signed_in: UI) -> None:
    room_id = await create_named(signed_in, unique("lab"))

    click(signed_in.user, f"delete-{room_id}")
    click(signed_in.user, "confirm")

    await should_notify(signed_in.user, "Room deleted")
    await signed_in.user.should_not_see(marker=f"room-{room_id}")
    assert room_id not in (await rooms_of(signed_in)).values()


async def test_cancel_changes_nothing(signed_in: UI) -> None:
    room_id = await create_named(signed_in, unique("lab"))

    click(signed_in.user, f"delete-{room_id}")
    click(signed_in.user, "cancel")

    assert room_id in (await rooms_of(signed_in)).values()


async def test_leave_a_room(signed_in: UI) -> None:
    room_id = await add_room_owned_elsewhere(signed_in, unique("lab"))
    await signed_in.user.open("/ui/rooms")

    click(signed_in.user, f"leave-{room_id}")
    click(signed_in.user, "confirm")

    await should_notify(signed_in.user, "You left the room")
    await signed_in.user.should_not_see(marker=f"room-{room_id}")
    assert room_id not in (await rooms_of(signed_in)).values()


async def test_owner_sees_no_leave(signed_in: UI) -> None:
    personal = await room_named(signed_in, "Personal")

    await signed_in.user.should_not_see(marker=f"leave-{personal}")


async def test_header_offers_logout(signed_in: UI) -> None:
    await signed_in.user.should_see(marker="logout")
    logout = next(iter(signed_in.user.find(marker="logout").elements))
    assert isinstance(logout, Html)
    assert 'action="/ui/logout"' in logout.content


async def test_action_is_refused_after_deactivation(signed_in: UI) -> None:
    await set_active(signed_in.db_path, signed_in.email, False)

    click(signed_in.user, "create-room")

    await should_notify(signed_in.user, "Please sign in again")
    assert len(await rooms_of(signed_in)) == 1
