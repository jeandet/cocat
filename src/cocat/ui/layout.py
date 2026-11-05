from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import urlencode

from fastapi import Request, status
from fastapi.responses import RedirectResponse
from nicegui import context, ui
from sqlalchemy.ext.asyncio import AsyncSession

from ..app.db import User
from ..app.rooms import RoomError
from ..app.urls import ui_url
from .context import UIContext

COOKIE_NAME = "fastapiusersauth"

T = TypeVar("T")


def login_link(request: Request, next_path: str) -> str:
    """Login page returning to `next_path`, a browser path; `urlencode` escapes `/`."""
    return ui_url(request, "/login") + "?" + urlencode({"next": next_path})


def redirect_page(
    target: str, status_code: int = status.HTTP_307_TEMPORARY_REDIRECT
) -> RedirectResponse:
    """Redirect from a page, after deleting the client NiceGUI built for it.

    NiceGUI collects a client when its socket disconnects, which never happens
    for a page that redirects before rendering, so each such visit would leak one.
    """
    context.client.delete()
    return RedirectResponse(target, status_code)


async def current_user(ctx: UIContext, request: Request) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    async with ctx.db.async_session_maker() as session:
        return await ctx.backend.resolve_user(session, token)


async def require_user(ctx: UIContext, request: Request) -> User | RedirectResponse:
    """The signed-in user, or a redirect to the login page."""
    user = await current_user(ctx, request)
    if user is not None:
        return user
    return redirect_page(login_link(request, request.url.path))


def page_header(request: Request, user: User) -> None:
    from .auth import logout_form  # auth imports this module for ui_url

    with ui.header().classes("items-center gap-4"):
        ui.label("cocat").classes("text-lg font-bold")
        ui.link("My rooms", ui_url(request, "/rooms")).mark("nav-rooms")
        ui.link("Account", ui_url(request, "/account")).mark("nav-account")
        ui.label(user.email)
        ui.html(logout_form(ui_url(request, "/logout"))).mark("logout")


async def perform(
    ctx: UIContext, work: Callable[[AsyncSession, User], Awaitable[T]]
) -> T | None:
    """Run a service call as the user behind the page's cookie, or refuse.

    A click handler runs outside the page request, whose session is closed by
    then, and the cookie is resolved again on every action so that an expired or
    deactivated session stops acting on a page that is still open.
    """
    async with ctx.db.async_session_maker() as session:
        user = await ctx.backend.resolve_user(
            session, context.client.request.cookies.get(COOKIE_NAME)
        )
        if user is None:
            ui.notify("Please sign in again")
            return None
        try:
            return await work(session, user)
        except RoomError as error:
            ui.notify(str(error))
            return None


async def act(
    ctx: UIContext, work: Callable[[AsyncSession, User], Awaitable[object]]
) -> bool:
    """`perform` for services that return nothing; `False` means refused."""

    async def capture(session: AsyncSession, user: User) -> bool:
        await work(session, user)
        return True

    return await perform(ctx, capture) is True


async def leave_room(ctx: UIContext, room_id: str) -> bool:
    """Drop the actor's membership through the action layer, not the bare service.

    Going through `RoomActions` also closes the leaver's room socket; without it
    a member who leaves keeps a live connection to a room they no longer own.
    """
    return await act(
        ctx,
        lambda session, user: ctx.actions.remove_member(
            session, room_id, user, user.id
        ),
    )


class ConfirmDialog:
    """The page's only confirmation dialog.

    `find` matches markers inside closed dialogs too, so one dialog per row would
    put two identical `confirm` buttons on a page with two rows and the click
    would go to the lowest id.
    """

    def __init__(self) -> None:
        self._action: Callable[[], Awaitable[None]] | None = None
        with ui.dialog() as self._dialog, ui.card():
            self._message = ui.label()
            self.field = ui.input("Name").mark("confirm-name")
            with ui.row():
                ui.button("Cancel", on_click=self._cancel).mark("cancel")
                ui.button("Confirm", on_click=self._confirm).mark("confirm")

    def ask(
        self,
        message: str,
        action: Callable[[], Awaitable[None]],
        name: str | None = None,
    ) -> None:
        """Ask before `action`; `name` also asks for a text, prefilled with it."""
        self._message.set_text(message)
        self.field.set_visibility(name is not None)
        self.field.set_value(name or "")
        self._action = action
        self._dialog.open()

    async def _confirm(self) -> None:
        self._dialog.close()
        if self._action is not None:
            await self._action()

    def _cancel(self) -> None:
        self._dialog.close()
        self._action = None
