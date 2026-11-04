from collections.abc import Callable

from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import ui

from ..app.db import Role
from ..app.rooms import create_room, list_user_rooms, remove_member, rename_room
from .context import UIContext
from .layout import ConfirmDialog, act, page_header, require_user, ui_url

Opener = Callable[[], None]


def register(ctx: UIContext) -> None:
    @ui.page("/rooms")
    async def rooms_page(request: Request) -> RedirectResponse | None:
        user = await require_user(ctx, request)
        if isinstance(user, RedirectResponse):
            return user
        page_header(request, user)
        dialog = ConfirmDialog()

        async def create() -> None:
            created = await act(
                ctx,
                lambda session, actor: create_room(session, actor, name.value or ""),
            )
            if created:
                name.set_value("")
                ui.notify("Room created")
                await room_list.refresh()

        def rename(room_id: str, current: str) -> None:
            async def apply() -> None:
                renamed = await act(
                    ctx,
                    lambda session, actor: rename_room(
                        session, room_id, actor, dialog.field.value or ""
                    ),
                )
                if renamed:
                    ui.notify("Room renamed")
                    await room_list.refresh()

            dialog.ask("Rename room", apply, name=current)

        def delete(room_id: str, room_name: str) -> None:
            async def apply() -> None:
                deleted = await act(
                    ctx,
                    lambda session, actor: ctx.actions.delete_room(
                        session, room_id, actor
                    ),
                )
                if deleted:
                    ui.notify("Room deleted")
                    await room_list.refresh()

            dialog.ask(f"Delete {room_name}?", apply)

        def leave(room_id: str, room_name: str) -> None:
            async def apply() -> None:
                left = await act(
                    ctx,
                    lambda session, actor: remove_member(
                        session, room_id, actor, actor.id
                    ),
                )
                if left:
                    ui.notify("You left the room")
                    await room_list.refresh()

            dialog.ask(f"Leave {room_name}?", apply)

        def actions(
            room_id: str, room_name: str, role: Role
        ) -> list[tuple[str, Opener]]:
            """What the role allows; an owner cannot leave and is never offered it."""
            if role is Role.owner:
                return [
                    ("Rename", lambda: rename(room_id, room_name)),
                    ("Delete", lambda: delete(room_id, room_name)),
                ]
            return [("Leave", lambda: leave(room_id, room_name))]

        @ui.refreshable
        async def room_list() -> None:
            async with ctx.db.async_session_maker() as session:
                rooms = await list_user_rooms(session, user.id)
            for room, role in rooms:
                with ui.row().classes("items-center gap-2"):
                    ui.link(room.name, ui_url(request, f"/rooms/{room.id}")).mark(
                        f"room-{room.id}"
                    )
                    ui.label(role.value)
                    for label, handler in actions(room.id, room.name, role):
                        ui.button(label, on_click=handler).mark(
                            f"{label.lower()}-{room.id}"
                        )

        with ui.row().classes("items-center gap-2"):
            name = ui.input("New room name").mark("new-room-name")
            ui.button("Create", on_click=create).mark("create-room")

        await room_list()
        return None
