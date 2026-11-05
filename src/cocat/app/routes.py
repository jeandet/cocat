from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from .actions import RoomActions
from .connections import Connections
from .db import DB, Role, User
from .invitations import (
    accept_invitation,
    create_invitation,
    list_invitations,
    preview_invitation,
    revoke_invitation,
)
from .mail import Mailer
from .rooms import (
    Conflict,
    Forbidden,
    NotFound,
    RoomError,
    create_room,
    list_members,
    list_user_rooms,
    rename_room,
    set_member_role,
    transfer_ownership,
)
from .schemas import (
    InvitationCreate,
    InvitationCreated,
    InvitationOut,
    InvitationPreviewOut,
    InvitationToken,
    MemberOut,
    OwnerTransfer,
    RoleUpdate,
    RoomCreate,
    RoomOut,
    RoomRename,
    UserRead,
)
from .users import Backend

ERROR_STATUS: dict[type[RoomError], int] = {
    RoomError: status.HTTP_400_BAD_REQUEST,
    NotFound: status.HTTP_404_NOT_FOUND,
    Forbidden: status.HTTP_403_FORBIDDEN,
    Conflict: status.HTTP_409_CONFLICT,
}


def room_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RoomError)
    return JSONResponse(
        status_code=ERROR_STATUS.get(type(exc), status.HTTP_400_BAD_REQUEST),
        content={"detail": str(exc)},
    )


def make_api_router(
    db: DB,
    backend: Backend,
    connections: Connections,
    update_dir: str,
    mail: Mailer | None = None,
) -> APIRouter:
    router = APIRouter()
    actions = RoomActions(connections, update_dir, mail)
    current_user = Depends(backend.current_active_user)
    session_dependency = Depends(db.get_async_session)

    @router.get("/me", response_model=UserRead)
    async def get_me(user: User = current_user) -> User:
        return user

    @router.get("/rooms", response_model=list[RoomOut])
    async def get_rooms(
        user: User = current_user, session: AsyncSession = session_dependency
    ) -> list[RoomOut]:
        return [
            RoomOut(id=room.id, name=room.name, role=role)
            for room, role in await list_user_rooms(session, user.id)
        ]

    @router.post("/rooms", response_model=RoomOut, status_code=status.HTTP_201_CREATED)
    async def post_room(
        body: RoomCreate,
        user: User = current_user,
        session: AsyncSession = session_dependency,
    ) -> RoomOut:
        room = await create_room(session, user, body.name)
        return RoomOut(id=room.id, name=room.name, role=Role.owner)

    @router.patch("/rooms/{room_id}", response_model=RoomOut)
    async def patch_room(
        room_id: str,
        body: RoomRename,
        user: User = current_user,
        session: AsyncSession = session_dependency,
    ) -> RoomOut:
        room = await rename_room(session, room_id, user, body.name)
        return RoomOut(id=room.id, name=room.name, role=Role.owner)

    @router.delete("/rooms/{room_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_room_route(
        room_id: str,
        user: User = current_user,
        session: AsyncSession = session_dependency,
    ) -> None:
        await actions.delete_room(session, room_id, user)

    @router.get("/rooms/{room_id}/members", response_model=list[MemberOut])
    async def get_members(
        room_id: str,
        user: User = current_user,
        session: AsyncSession = session_dependency,
    ) -> list[MemberOut]:
        return [
            MemberOut(user_id=member.id, email=member.email, role=role)
            for member, role in await list_members(session, room_id, user)
        ]

    @router.patch(
        "/rooms/{room_id}/members/{user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def patch_member(
        room_id: str,
        user_id: UUID,
        body: RoleUpdate,
        user: User = current_user,
        session: AsyncSession = session_dependency,
    ) -> None:
        await set_member_role(session, room_id, user, user_id, Role(body.role))

    @router.delete(
        "/rooms/{room_id}/members/{user_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_member(
        room_id: str,
        user_id: UUID,
        user: User = current_user,
        session: AsyncSession = session_dependency,
    ) -> None:
        await actions.remove_member(session, room_id, user, user_id)

    @router.post("/rooms/{room_id}/owner", status_code=status.HTTP_204_NO_CONTENT)
    async def post_owner(
        room_id: str,
        body: OwnerTransfer,
        user: User = current_user,
        session: AsyncSession = session_dependency,
    ) -> None:
        await transfer_ownership(session, room_id, user, body.user_id)

    @router.post(
        "/rooms/{room_id}/invitations",
        response_model=InvitationCreated,
        status_code=status.HTTP_201_CREATED,
    )
    async def post_invitation(
        request: Request,
        room_id: str,
        body: InvitationCreate,
        user: User = current_user,
        session: AsyncSession = session_dependency,
    ) -> InvitationCreated:
        if body.email is None:
            invitation, token = await create_invitation(
                session,
                room_id,
                user,
                Role(body.role),
                expires_in=timedelta(days=body.expires_in_days),
                max_uses=body.max_uses,
            )
        else:
            invitation, token = await actions.invite(
                session, room_id, user, Role(body.role), body.email, request
            )
        return InvitationCreated(id=invitation.id, token=token)

    @router.get("/rooms/{room_id}/invitations", response_model=list[InvitationOut])
    async def get_invitations(
        room_id: str,
        user: User = current_user,
        session: AsyncSession = session_dependency,
    ) -> list[InvitationOut]:
        return [
            InvitationOut.model_validate(invitation)
            for invitation in await list_invitations(session, room_id, user)
        ]

    @router.delete(
        "/rooms/{room_id}/invitations/{invitation_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_invitation(
        room_id: str,
        invitation_id: int,
        user: User = current_user,
        session: AsyncSession = session_dependency,
    ) -> None:
        await revoke_invitation(session, room_id, user, invitation_id)

    @router.post("/invitations/preview", response_model=InvitationPreviewOut)
    async def post_preview(
        body: InvitationToken,
        session: AsyncSession = session_dependency,
    ) -> InvitationPreviewOut:
        return InvitationPreviewOut.model_validate(
            await preview_invitation(session, body.token)
        )

    @router.post("/invitations/accept", response_model=RoomOut)
    async def post_accept(
        body: InvitationToken,
        user: User = current_user,
        session: AsyncSession = session_dependency,
    ) -> RoomOut:
        room, role = await accept_invitation(session, body.token, user)
        return RoomOut(id=room.id, name=room.name, role=role)

    return router
