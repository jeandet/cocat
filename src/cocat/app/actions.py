from dataclasses import dataclass
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from .connections import Connections
from .db import User
from .rooms import delete_room, remove_member
from .storage import remove_room_file


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
