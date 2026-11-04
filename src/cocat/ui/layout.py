import os
from collections.abc import Awaitable, Callable
from typing import TypeVar
from urllib.parse import urlencode

from fastapi import Request, status
from fastapi.responses import RedirectResponse
from nicegui import context, ui
from sqlalchemy.ext.asyncio import AsyncSession

from ..app.db import User
from ..app.rooms import RoomError
from .context import PAGES_MOUNT, UIContext

COOKIE_NAME = "fastapiusersauth"

T = TypeVar("T")


def ui_url(request: Request, path: str) -> str:
    """Absolute browser path of a UI page, reverse-proxy prefix included.

    Measured in all four cases: inside a page `root_path` is already `/ui`, and
    `app_root_path` carries the proxy prefix; on a host route there is no
    `app_root_path` and `root_path` is the prefix alone. Guessing this from
    `root_path` alone drops the mount on host routes.
    """
    base = request.scope.get("app_root_path", request.scope.get("root_path", ""))
    return base + PAGES_MOUNT + path


def login_link(request: Request, next_path: str) -> str:
    """Login page returning to `next_path`, a browser path; `urlencode` escapes `/`."""
    return ui_url(request, "/login") + "?" + urlencode({"next": next_path})


def public_base(request: Request) -> str:
    """Origin other people reach the app at, for the links we hand out.

    A share link is opened from someone else's browser, so its origin is not the
    Host header of the request that created it; a deployment behind a proxy sets
    `COCAT_PUBLIC_URL` instead.
    """
    configured = os.environ.get("COCAT_PUBLIC_URL")
    if configured:
        return configured.rstrip("/")
    return f"{request.url.scheme}://{request.url.netloc}"


def absolute_url(request: Request, path: str) -> str:
    return public_base(request) + ui_url(request, path)


async def current_user(ctx: UIContext, request: Request) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    async with ctx.db.async_session_maker() as session:
        return await ctx.backend.resolve_user(session, token)


async def require_user(ctx: UIContext, request: Request) -> User | RedirectResponse:
    """The signed-in user, or a redirect to the login page.

    The page's client is deleted before redirecting: NiceGUI creates one per page
    request and only collects it when a socket disconnects, which never happens
    for a page that redirects away, so every signed-out visit would leak one.
    """
    user = await current_user(ctx, request)
    if user is not None:
        return user
    context.client.delete()
    return RedirectResponse(
        login_link(request, request.url.path), status.HTTP_307_TEMPORARY_REDIRECT
    )


def page_header(request: Request, user: User) -> None:
    from .auth import logout_form  # auth imports this module for ui_url

    with ui.header().classes("items-center gap-4"):
        ui.label("cocat").classes("text-lg font-bold")
        ui.link("My rooms", ui_url(request, "/rooms")).mark("nav-rooms")
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
