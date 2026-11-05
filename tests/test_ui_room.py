from uuid import uuid4

import pytest
from conftest import UI
from nicegui.elements.input import Input
from sqlalchemy.ext.asyncio import AsyncSession
from utils import (
    click,
    connect,
    ensure_user,
    in_session,
    rooms_of,
    should_notify,
    user_by_email,
    user_id,
)

from cocat.app.db import Invitation, Role, utcnow
from cocat.app.invitations import (
    INVITATION_LIFETIME,
    accept_invitation,
    create_invitation,
    list_invitations,
)
from cocat.app.rooms import add_member, create_room, remove_member

pytestmark = pytest.mark.anyio


async def shared_room(db_path: str, owner_email: str, member_email: str) -> str:
    await ensure_user(db_path, owner_email)
    await ensure_user(db_path, member_email)

    async def share(session: AsyncSession) -> str:
        owner = await user_by_email(session, owner_email)
        room = await create_room(session, owner, f"shared-{uuid4().hex}")
        member = await user_by_email(session, member_email)
        await add_member(session, room.id, member.id, Role.editor)
        return room.id

    return await in_session(db_path, share)


async def personal_room(ui: UI) -> str:
    return (await rooms_of(ui.db_path, ui.email))["Personal"]


async def invitation_list(ui: UI, room_id: str) -> list[Invitation]:
    async def rows(session: AsyncSession) -> list[Invitation]:
        return await list_invitations(
            session, room_id, await user_by_email(session, ui.email)
        )

    return await in_session(ui.db_path, rows)


def value_of(ui: UI, marker: str) -> str:
    element = next(iter(ui.user.find(marker=marker).elements))
    assert isinstance(element, Input)
    return str(element.value)


async def open_room(ui: UI, room_id: str) -> None:
    await ui.log_in()
    await ui.user.open(f"/ui/rooms/{room_id}")


async def test_requires_login(ui: UI) -> None:
    await ui.user.open("/ui/rooms/nope")

    await ui.user.should_see("Sign in")


async def test_unknown_room_is_not_found(ui: UI) -> None:
    await ui.log_in()
    await ui.user.open("/ui/rooms/nope")

    await ui.user.should_see("Room not found")


async def test_owner_sees_members(ui: UI) -> None:
    await open_room(ui, await personal_room(ui))
    owner_id = await user_id(ui.db_path, ui.email)

    await ui.user.should_see(marker=f"member-{owner_id}", content=ui.email)
    await ui.user.should_see("owner")
    await ui.user.should_see(marker="create-invitation")
    await ui.user.should_not_see(marker=f"remove-{owner_id}")


async def test_editor_sees_a_read_only_table(ui: UI) -> None:
    owner_email = f"owner-{uuid4().hex}@foo.com"
    room_id = await shared_room(ui.db_path, owner_email, ui.email)
    await open_room(ui, room_id)
    owner_id = await user_id(ui.db_path, owner_email)

    await ui.user.should_see(marker="leave-room")
    await ui.user.should_see("Only the owner can manage invitations.")
    await ui.user.should_not_see(marker="create-invitation")
    await ui.user.should_not_see(marker=f"remove-{owner_id}")
    await ui.user.should_not_see(marker=f"transfer-{owner_id}")


async def test_transfer_ownership(ui: UI) -> None:
    other_email = f"other-{uuid4().hex}@foo.com"
    room_id = await shared_room(ui.db_path, ui.email, other_email)
    await open_room(ui, room_id)
    other_id = await user_id(ui.db_path, other_email)

    click(ui.user, f"transfer-{other_id}")
    click(ui.user, "confirm")

    await should_notify(ui.user, f"{other_email} now owns the room")
    await ui.user.should_not_see(marker="create-invitation", retries=10)
    await ui.user.should_not_see(marker=f"transfer-{other_id}", retries=10)


async def test_remove_member(ui: UI) -> None:
    other_email = f"other-{uuid4().hex}@foo.com"
    room_id = await shared_room(ui.db_path, ui.email, other_email)
    await open_room(ui, room_id)
    other_id = await user_id(ui.db_path, other_email)

    click(ui.user, f"remove-{other_id}")
    click(ui.user, "confirm")

    await should_notify(ui.user, f"Removed {other_email}")
    await ui.user.should_not_see(marker=f"member-{other_id}", retries=10)


async def test_create_share_link(ui: UI) -> None:
    await open_room(ui, await personal_room(ui))

    click(ui.user, "create-invitation")

    await should_notify(ui.user, "Share link created")
    await ui.user.should_see("https://testserver/ui/invitation#", retries=10)
    token = value_of(ui, "share-link").rsplit("#", 1)[1]
    assert token
    assert all(token not in message for message in ui.user.notify.messages)


async def test_share_link_uses_the_public_url(ui: UI, monkeypatch) -> None:
    # A path in COCAT_PUBLIC_URL would duplicate the proxy prefix.
    monkeypatch.setenv("COCAT_PUBLIC_URL", "https://cocat.example.org/cocat/")
    await open_room(ui, await personal_room(ui))

    click(ui.user, "create-invitation")

    await ui.user.should_see("https://cocat.example.org/ui/invitation#", retries=10)


async def test_share_link_copies(ui: UI) -> None:
    await open_room(ui, await personal_room(ui))
    click(ui.user, "create-invitation")
    await ui.user.should_see("https://testserver/ui/invitation#", retries=30)
    link = value_of(ui, "share-link")

    click(ui.user, "copy-link")

    await should_notify(ui.user, "Link copied")
    assert value_of(ui, "share-link") == link


async def test_revoke_invitation(ui: UI) -> None:
    room_id = await personal_room(ui)
    await open_room(ui, room_id)
    click(ui.user, "create-invitation")
    await should_notify(ui.user, "Share link created")
    invitation = (await invitation_list(ui, room_id))[0]

    click(ui.user, f"revoke-{invitation.id}")
    click(ui.user, "confirm")

    await should_notify(ui.user, "Invitation revoked")
    await ui.user.should_not_see(marker=f"invitation-{invitation.id}", retries=10)


async def test_invitation_row_shows_usage(ui: UI) -> None:
    room_id = await personal_room(ui)
    guest_email = f"guest-{uuid4().hex}@foo.com"
    await ensure_user(ui.db_path, guest_email)

    async def invite_and_accept(session: AsyncSession) -> None:
        owner = await user_by_email(session, ui.email)
        guest = await user_by_email(session, guest_email)
        await create_invitation(session, room_id, owner, Role.editor, email=guest_email)
        _, token = await create_invitation(
            session, room_id, owner, Role.editor, max_uses=2
        )
        await accept_invitation(session, token, guest)

    await in_session(ui.db_path, invite_and_accept)
    expires = (utcnow() + INVITATION_LIFETIME).strftime("%Y-%m-%d")

    await open_room(ui, room_id)

    await ui.user.should_see(guest_email)
    await ui.user.should_see("1/2")
    await ui.user.should_see("0/1")
    await ui.user.should_see(expires)


async def test_action_is_refused_after_losing_access(ui: UI) -> None:
    owner_email = f"owner-{uuid4().hex}@foo.com"
    room_id = await shared_room(ui.db_path, owner_email, ui.email)
    await open_room(ui, room_id)

    async def drop(session: AsyncSession) -> None:
        owner = await user_by_email(session, owner_email)
        member = await user_by_email(session, ui.email)
        await remove_member(session, room_id, owner, member.id)

    await in_session(ui.db_path, drop)
    click(ui.user, "leave-room")
    click(ui.user, "confirm")

    await should_notify(ui.user, f"Room {room_id} not found")


async def test_leave_the_room(ui: UI) -> None:
    owner_email = f"owner-{uuid4().hex}@foo.com"
    room_id = await shared_room(ui.db_path, owner_email, ui.email)
    await open_room(ui, room_id)
    socket = connect(ui.cocat.connections, room_id, await user_id(ui.db_path, ui.email))

    click(ui.user, "leave-room")
    click(ui.user, "confirm")

    await should_notify(ui.user, "You left the room")
    assert socket.closed == [1008]
    await ui.user.should_not_see(marker=f"room-{room_id}", retries=10)


@pytest.fixture()
def anyio_backend() -> str:
    """aiosqlite only runs on asyncio, so these tests cannot use trio."""
    return "asyncio"
