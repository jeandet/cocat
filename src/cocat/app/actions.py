from dataclasses import dataclass
from uuid import UUID

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from .connections import Connections
from .db import Invitation, Role, Room, User
from .invitations import create_invitation
from .mail import Mailer, MailError, invitation_mail
from .rooms import RoomError, delete_room, remove_member
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

    async def remove_member(
        self,
        session: AsyncSession,
        room_id: str,
        actor: User,
        member_id: UUID,
    ) -> None:
        await remove_member(session, room_id, actor, member_id)
        await self.connections.close(room_id, member_id)

    async def invite(
        self,
        session: AsyncSession,
        room_id: str,
        actor: User,
        role: Role,
        email: str,
        request: Request,
    ) -> tuple[Invitation, str]:
        """Create an invitation and mail it when a mailer is configured.

        Without SMTP the raw token comes back: the sender has no other way to
        hand the link over than to copy it out of the page.
        """
        invitation, token = await create_invitation(
            session, room_id, actor, role, email=email
        )
        if self.mail is None:
            return invitation, token
        room = await session.get_one(Room, room_id)
        try:
            await self.mail.send(
                email,
                *invitation_mail(
                    room.name,
                    actor.email,
                    absolute_url(request, f"/invitation#{token}"),
                ),
            )
        except MailError as error:
            raise RoomError(
                "The invitation was created but the email could not be sent"
            ) from error
        return invitation, ""
