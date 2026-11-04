from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeVar
from uuid import uuid4

import pytest
from conftest import UI
from nicegui.client import Client
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from utils import click, fill, set_active, should_notify

from cocat.app.db import Role, User, get_db
from cocat.app.rooms import add_member, create_room, list_user_rooms
from cocat.cli import _create_user

pytestmark = pytest.mark.anyio

T = TypeVar("T")


@pytest.fixture()
def anyio_backend() -> str:
    # aiosqlite only runs on asyncio
    return "asyncio"


async def in_session(ui: UI, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    db = get_db(ui.db_path)
    try:
        async with db.async_session_maker() as session:
            return await work(session)
    finally:
        await db.engine.dispose()


async def user_by_email(session: AsyncSession, email: str) -> User:
    user = await session.scalar(select(User).filter_by(email=email))
    assert user is not None
    return user


async def rooms_of(ui: UI) -> dict[str, str]:
    async def rooms(session: AsyncSession) -> dict[str, str]:
        user = await user_by_email(session, ui.email)
        return {
            room.name: room.id for room, _ in await list_user_rooms(session, user.id)
        }

    return await in_session(ui, rooms)


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

    return await in_session(ui, add)


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


async def test_action_is_refused_after_deactivation(signed_in: UI) -> None:
    await set_active(signed_in.db_path, signed_in.email, False)

    click(signed_in.user, "create-room")

    await should_notify(signed_in.user, "Please sign in again")
    assert len(await rooms_of(signed_in)) == 1
