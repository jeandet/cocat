from uuid import UUID

from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import ui
from sqlalchemy.ext.asyncio import AsyncSession

from ..app.db import Invitation, Role, Room, User
from ..app.invitations import create_invitation, list_invitations, revoke_invitation
from ..app.rooms import get_role, list_members, transfer_ownership
from .context import UIContext
from .layout import (
    ConfirmDialog,
    absolute_url,
    act,
    leave_room,
    page_header,
    perform,
    require_user,
    ui_url,
)


def usage(invitation: Invitation) -> str:
    """`uses/max_uses` for a limited invitation, nothing for an unlimited one."""
    if invitation.max_uses is None:
        return ""
    return f"{invitation.uses}/{invitation.max_uses}"


def register(ctx: UIContext) -> None:
    @ui.page("/rooms/{room_id}")
    async def room_page(request: Request, room_id: str) -> RedirectResponse | None:
        user = await require_user(ctx, request)
        if isinstance(user, RedirectResponse):
            return user
        page_header(request, user)
        async with ctx.db.async_session_maker() as session:
            room = await session.get(Room, room_id)
            role = (
                await get_role(session, room_id, user.id) if room is not None else None
            )
            room_name = room.name if room is not None else ""
        if role is None:
            ui.label("Room not found")
            ui.link("Back to my rooms", ui_url(request, "/rooms")).mark("back-to-rooms")
            return None

        ui.label(room_name).classes("text-2xl")
        ui.link("My rooms", ui_url(request, "/rooms")).mark("back-to-rooms")
        dialog = ConfirmDialog()
        link = ""

        def leave() -> None:
            async def apply() -> None:
                if await leave_room(ctx, room_id):
                    ui.notify("You left the room")
                    ui.navigate.to(ui_url(request, "/rooms"))

            dialog.ask(f"Leave {room_name}?", apply)

        @ui.refreshable
        async def heading() -> None:
            async with ctx.db.async_session_maker() as session:
                current = await get_role(session, room_id, user.id)
            assert current is not None  # the page is only built for members
            ui.label(f"You are an {current.value}")
            if current is not Role.owner:
                ui.button("Leave", on_click=leave).mark("leave-room")

        def remove(member_id: UUID, email: str) -> None:
            async def apply() -> None:
                removed = await act(
                    ctx,
                    lambda session, actor: ctx.actions.remove_member(
                        session, room_id, actor, member_id
                    ),
                )
                if removed:
                    ui.notify(f"Removed {email}")
                    await members.refresh()

            dialog.ask(f"Remove {email}?", apply)

        def transfer(member_id: UUID, email: str) -> None:
            async def apply() -> None:
                transferred = await act(
                    ctx,
                    lambda session, actor: transfer_ownership(
                        session, room_id, actor, member_id
                    ),
                )
                if transferred:
                    ui.notify(f"{email} now owns the room")
                    await refresh_all()

            dialog.ask(f"Make {email} the owner?", apply)

        def revoke(invitation_id: int) -> None:
            async def apply() -> None:
                revoked = await act(
                    ctx,
                    lambda session, actor: revoke_invitation(
                        session, room_id, actor, invitation_id
                    ),
                )
                if revoked:
                    ui.notify("Invitation revoked")
                    await invitations.refresh()

            dialog.ask("Revoke this invitation?", apply)

        async def create_link() -> None:
            nonlocal link

            async def work(session: AsyncSession, actor: User) -> str:
                _, token = await create_invitation(session, room_id, actor, Role.editor)
                return absolute_url(request, f"/invitation#{token}")

            link = await perform(ctx, work) or ""
            if link:
                ui.notify("Share link created")
                await invitations.refresh()

        def copy_link() -> None:
            ui.clipboard.write(link)
            ui.notify("Link copied")

        async def refresh_all() -> None:
            await heading.refresh()
            await members.refresh()
            await invitations.refresh()

        @ui.refreshable
        async def members() -> None:
            async with ctx.db.async_session_maker() as session:
                actor = await session.get_one(User, user.id)
                current = await get_role(session, room_id, actor.id)
                rows = await list_members(session, room_id, actor)
            ui.label("Members").classes("text-xl")
            for member, member_role in rows:
                with ui.row().classes("items-center gap-2"):
                    ui.label(member.email).mark(f"member-{member.id}")
                    ui.label(member_role.value)
                    if current is Role.owner and member_role is not Role.owner:
                        ui.button(
                            "Remove",
                            on_click=lambda id=member.id, email=member.email: remove(
                                id, email
                            ),
                        ).mark(f"remove-{member.id}")
                        ui.button(
                            "Transfer ownership",
                            on_click=lambda id=member.id, email=member.email: transfer(
                                id, email
                            ),
                        ).mark(f"transfer-{member.id}")

        @ui.refreshable
        async def invitations() -> None:
            async with ctx.db.async_session_maker() as session:
                actor = await session.get_one(User, user.id)
                current = await get_role(session, room_id, actor.id)
                if current is not Role.owner:
                    ui.label("Only the owner can manage invitations.")
                    return
                rows = await list_invitations(session, room_id, actor)
            ui.label("Invitations").classes("text-xl")
            ui.input("Share link", value=link).props("readonly").mark("share-link")
            with ui.row().classes("items-center gap-2"):
                ui.button("Create share link", on_click=create_link).mark(
                    "create-invitation"
                )
                ui.button("Copy", on_click=copy_link).mark("copy-link")
            for invitation in rows:
                with ui.row().classes("items-center gap-2"):
                    ui.label(invitation.email or "Share link").mark(
                        f"invitation-{invitation.id}"
                    )
                    ui.label(invitation.role.value)
                    ui.label(invitation.expires_at.strftime("%Y-%m-%d"))
                    if usage(invitation):
                        ui.label(usage(invitation))
                    ui.button(
                        "Revoke", on_click=lambda id=invitation.id: revoke(id)
                    ).mark(f"revoke-{invitation.id}")

        await heading()
        await members()
        await invitations()
        return None
