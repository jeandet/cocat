from datetime import timedelta

import pytest
from conftest import make_orm_user
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from utils import RecordingMail, connect

from cocat.app.actions import RoomActions
from cocat.app.connections import Connections
from cocat.app.db import Role, utcnow
from cocat.app.invitations import list_invitations
from cocat.app.mail import MailError
from cocat.app.rooms import (
    RoomError,
    add_member,
    create_room,
    get_role,
    list_user_rooms,
)
from cocat.app.storage import room_file

pytestmark = pytest.mark.anyio


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


@pytest.fixture()
def anyio_backend() -> str:
    """aiosqlite only runs on asyncio, so these tests cannot use trio."""
    return "asyncio"


def make_request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "server": ("testserver", 443),
            "path": "/api/rooms/lab/invitations",
            "root_path": "",
            "query_string": b"",
            "headers": [],
        }
    )


async def test_invite_without_a_mailer_returns_the_token(
    session: AsyncSession, update_dir: str
) -> None:
    owner = await make_orm_user(session, "owner@example.com")
    room = await create_room(session, owner, "Lab")
    actions = RoomActions(Connections(), update_dir)

    invitation, token = await actions.invite(
        session, room.id, owner, Role.editor, "guest@example.org", make_request()
    )

    assert token
    assert invitation.email == "guest@example.org"


async def test_invite_with_a_mailer_sends_the_link(
    session: AsyncSession, update_dir: str
) -> None:
    owner = await make_orm_user(session, "owner@example.com")
    room = await create_room(session, owner, "Lab")
    mail = RecordingMail()
    actions = RoomActions(Connections(), update_dir, mail)

    invitation, token = await actions.invite(
        session, room.id, owner, Role.editor, "guest@example.org", make_request()
    )

    assert token == ""
    assert invitation.email == "guest@example.org"
    [(_to, _subject, body)] = mail.mails_to("guest@example.org")
    assert "https://testserver/ui/invitation#" in body
    assert "Lab" in body


async def test_invite_reports_a_mailer_that_fails(
    session: AsyncSession, update_dir: str
) -> None:
    owner = await make_orm_user(session, "owner@example.com")
    room = await create_room(session, owner, "Lab")

    class RefusingMail:
        async def send(self, to: str, subject: str, body: str) -> None:
            raise MailError("refused")

    actions = RoomActions(Connections(), update_dir, RefusingMail())

    with pytest.raises(RoomError, match="could not be sent"):
        await actions.invite(
            session, room.id, owner, Role.editor, "guest@example.org", make_request()
        )

    assert await list_invitations(session, room.id, owner) == []


async def test_invite_refuses_an_address_that_is_not_an_email(
    session: AsyncSession, update_dir: str
) -> None:
    owner = await make_orm_user(session, "owner@example.com")
    room = await create_room(session, owner, "Lab")
    actions = RoomActions(Connections(), update_dir)

    with pytest.raises(RoomError, match="valid email address"):
        await actions.invite(
            session, room.id, owner, Role.editor, "not-an-address", make_request()
        )


async def test_invite_refuses_a_placeholder_address(
    session: AsyncSession, update_dir: str
) -> None:
    owner = await make_orm_user(session, "owner@example.com")
    room = await create_room(session, owner, "Lab")
    actions = RoomActions(Connections(), update_dir)

    with pytest.raises(RoomError, match="valid email address"):
        await actions.invite(
            session,
            room.id,
            owner,
            Role.editor,
            "orcid-1@orcid.placeholder.cocat.org",
            make_request(),
        )


async def test_invite_forwards_the_requested_lifetime(
    session: AsyncSession, update_dir: str
) -> None:
    owner = await make_orm_user(session, "owner@example.com")
    room = await create_room(session, owner, "Lab")
    actions = RoomActions(Connections(), update_dir)

    await actions.invite(
        session,
        room.id,
        owner,
        Role.editor,
        "guest@example.org",
        make_request(),
        timedelta(days=3),
    )

    [invitation] = await list_invitations(session, room.id, owner)
    assert invitation.expires_at < utcnow() + timedelta(days=4)


async def test_set_member_role_closes_that_socket(
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

    await actions.set_member_role(session, room.id, owner, member.id, Role.viewer)

    assert member_socket.closed == [1008]
    assert owner_socket.closed == []
    assert await get_role(session, room.id, member.id) is Role.viewer
