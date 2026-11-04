from typing import cast
from uuid import UUID

import pytest
from conftest import make_orm_user
from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from cocat.app.actions import RoomActions
from cocat.app.connections import Connections
from cocat.app.db import Role
from cocat.app.rooms import add_member, create_room, get_role, list_user_rooms
from cocat.app.storage import room_file

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    # aiosqlite only runs on asyncio
    return "asyncio"


class FakeWebSocket:
    def __init__(self) -> None:
        self.closed: list[int] = []

    async def close(self, code: int = 1000) -> None:
        self.closed.append(code)


def connect(connections: Connections, room_id: str, user_id: UUID) -> FakeWebSocket:
    socket = FakeWebSocket()
    connections.add(room_id, user_id, cast(WebSocket, socket))
    return socket


async def test_delete_room_closes_sockets_and_removes_the_file(
    session: AsyncSession, update_dir: str
) -> None:
    owner = await make_orm_user(session, "owner@example.com")
    room = await create_room(session, owner, "Room")
    room_file(update_dir, room.id).write_text("stale")
    connections = Connections()
    socket = connect(connections, room.id, owner.id)
    actions = RoomActions(connections, update_dir)

    await actions.delete_room(session, room.id, owner)

    assert socket.closed == [1008]
    assert not room_file(update_dir, room.id).exists()
    assert await list_user_rooms(session, owner.id) == []


async def test_remove_member_closes_that_socket(
    session: AsyncSession, update_dir: str
) -> None:
    owner = await make_orm_user(session, "owner@example.com")
    member = await make_orm_user(session, "member@example.com")
    room = await create_room(session, owner, "Room")
    await add_member(session, room.id, member.id, Role.editor)
    connections = Connections()
    member_socket = connect(connections, room.id, member.id)
    owner_socket = connect(connections, room.id, owner.id)
    actions = RoomActions(connections, update_dir)

    await actions.remove_member(session, room.id, owner, member.id)

    assert member_socket.closed == [1008]
    assert owner_socket.closed == []
    assert await get_role(session, room.id, member.id) is None
