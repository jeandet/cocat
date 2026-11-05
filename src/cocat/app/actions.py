from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from fastapi import Request
from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from .connections import Connections
from .db import Invitation, Role, Room, User
from .invitations import INVITATION_LIFETIME, accept_invitation, create_invitation
from .mail import Mailer, MailError, invitation_mail, is_placeholder
from .rooms import (
    RoomError,
    delete_room,
    remove_member,
    set_member_role,
    transfer_ownership,
)
from .storage import remove_room_file
from .urls import absolute_url


@dataclass(frozen=True)
class RoomActions:
    """Room changes that also revoke access.

    Closing the sockets and removing the room file is what actually revokes
    access, so it lives here rather than in each front end: two callers that
    each remember the cleanup will drift, and a forgotten step leaves a removed
    member connected.
    """

    connections: Connections
    update_dir: str
    mail: Mailer | None = None

    async def delete_room(
        self, session: AsyncSession, room_id: str, actor: User
    ) -> None:
        await delete_room(session, room_id, actor)
        await self.connections.close(room_id)
        remove_room_file(self.update_dir, room_id)

    async def set_member_role(
        self,
        session: AsyncSession,
        room_id: str,
        actor: User,
        member_id: UUID,
        role: Role,
    ) -> None:
        """Change a member's role and drop the sockets their old role allowed."""
        await set_member_role(session, room_id, actor, member_id, role)
        await self.connections.close(room_id, member_id)

    async def remove_member(
        self,
        session: AsyncSession,
        room_id: str,
        actor: User,
        member_id: UUID,
    ) -> None:
        await remove_member(session, room_id, actor, member_id)
        await self.connections.close(room_id, member_id)

    async def transfer_ownership(
        self,
        session: AsyncSession,
        room_id: str,
        actor: User,
        member_id: UUID,
    ) -> None:
        """Make a member the owner and drop their sockets.

        The role decides what a socket may write when it connects, so a member
        promoted mid-session keeps the older role until it reconnects.
        """
        await transfer_ownership(session, room_id, actor, member_id)
        await self.connections.close(room_id, member_id)

    async def accept_invitation(
        self,
        session: AsyncSession,
        token: str,
        user: User,
    ) -> tuple[Room, Role]:
        """Join the room an invitation points at, dropping the user's sockets.

        An invitation can raise an existing member's role, and that member may
        already have a socket open with the older one.
        """
        room, role = await accept_invitation(session, token, user)
        await self.connections.close(room.id, user.id)
        return room, role

    async def invite(
        self,
        session: AsyncSession,
        room_id: str,
        actor: User,
        role: Role,
        email: str,
        request: Request,
        expires_in: timedelta = INVITATION_LIFETIME,
    ) -> tuple[Invitation, str]:
        """Create an invitation and mail it when a mailer is configured.

        Without SMTP the raw token comes back: the sender has no other way to
        hand the link over than to copy it out of the page. With SMTP the token
        stays here — a mail that failed is undone rather than left behind for
        nobody to use.
        """
        address = _address(email)
        invitation, token = await create_invitation(
            session, room_id, actor, role, email=address, expires_in=expires_in
        )
        if self.mail is None:
            return invitation, token
        room = await session.get_one(Room, room_id)
        try:
            await self.mail.send(
                address,
                *invitation_mail(
                    room.name,
                    actor.email,
                    absolute_url(request, f"/invitation#{token}"),
                ),
            )
        except MailError as error:
            await session.delete(invitation)
            await session.commit()
            raise RoomError(
                "The email could not be sent; no invitation was created"
            ) from error
        return invitation, ""


ADDRESS = TypeAdapter(EmailStr)


def _address(email: str) -> str:
    """The address to invite, or a refusal both front ends share."""
    stripped = email.strip()
    if not stripped or is_placeholder(stripped):
        raise RoomError("Enter a valid email address")
    try:
        return str(ADDRESS.validate_python(stripped))
    except ValidationError as error:
        raise RoomError("Enter a valid email address") from error
