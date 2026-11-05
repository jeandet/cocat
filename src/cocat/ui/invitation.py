from fastapi import Request
from nicegui import ui
from sqlalchemy.ext.asyncio import AsyncSession

from ..app.db import User
from ..app.invitations import accept_invitation, preview_invitation
from ..app.rooms import RoomError
from .context import UIContext
from .layout import current_user, login_link, page_header, perform, ui_url

FRAGMENT_SCRIPT = "window.location.hash.replace(/^#/, '')"
CLEAR_FRAGMENT_SCRIPT = (
    "history.replaceState(null, '', window.location.pathname + window.location.search)"
)


async def read_fragment_token() -> str:
    """Ask the browser for its fragment, which never reaches the server.

    A token sent in a path or a query ends up in access logs and in the history
    of every machine it passes; the answer to this call arrives in a socket
    message body instead, and only lives in the page's Python scope.
    """
    try:
        value = await ui.run_javascript(FRAGMENT_SCRIPT)
    except TimeoutError:
        # A blocked socket or disabled JavaScript must not leave a blank page.
        return ""
    return "" if value is None else str(value).strip()


def token_from_text(text: str) -> str:
    """The token of a pasted share link, or the pasted text when it is bare.

    Signing in leaves the page behind and a fragment does not survive that, so a
    visitor who has to log in first pastes the link again on the page.
    """
    return text.partition("#")[2].strip() or text.strip()


def register(ctx: UIContext) -> None:
    @ui.page("/invitation")
    async def invitation_page(request: Request) -> None:
        user = await current_user(ctx, request)
        if user is not None:
            page_header(request, user)
        ui.label("Join a room").classes("text-2xl")
        token = await read_fragment_token()
        if token:
            ui.run_javascript(CLEAR_FRAGMENT_SCRIPT)
        state = {"token": token}

        async def join(session: AsyncSession, actor: User) -> tuple[str, str]:
            room, _ = await accept_invitation(session, state["token"], actor)
            return room.id, room.name

        async def accept() -> None:
            joined = await perform(ctx, join)
            if joined is None:
                return
            room_id, room_name = joined
            ui.notify(f"You joined {room_name}")
            ui.navigate.to(ui_url(request, f"/rooms/{room_id}"))

        @ui.refreshable
        async def invitation() -> None:
            if not state["token"]:
                pasted = ui.input("Invitation link").mark("paste-link")

                async def use_pasted() -> None:
                    state["token"] = token_from_text(pasted.value or "")
                    await invitation.refresh()

                ui.button("Continue", on_click=use_pasted).mark("continue")
                return
            async with ctx.db.async_session_maker() as session:
                try:
                    preview = await preview_invitation(session, state["token"])
                except RoomError:
                    ui.label("This invitation is not valid anymore")
                    return
            ui.label(f"{preview.inviter_email} invites you to {preview.room_name}")
            ui.label(f"You join as {preview.role.value}")
            ui.label(f"The invitation lasts until {preview.expires_at:%Y-%m-%d}")
            if user is None:
                link = login_link(request, ui_url(request, "/invitation"))
                ui.link("Log in to accept", link).mark("login-to-accept")
                ui.label("Signing in leaves this page behind: paste the link again.")
            else:
                ui.button("Accept", on_click=accept).mark("accept-invitation")

        await invitation()
