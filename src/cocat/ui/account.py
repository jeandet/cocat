from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import ui

from ..app.schemas import UserUpdate
from .auth import authenticate
from .context import UIContext
from .layout import COOKIE_NAME, page_header, require_user


def register(ctx: UIContext) -> None:
    @ui.page("/account")
    async def account_page(request: Request) -> RedirectResponse | None:
        user = await require_user(ctx, request)
        if isinstance(user, RedirectResponse):
            return user
        page_header(request, user)
        ui.label("Account").classes("text-2xl")
        ui.label(user.email).mark("account-email")
        current = ui.input("Current password").props("type=password")
        current.mark("current-password")
        new = ui.input("New password").props("type=password")
        new.mark("new-password")
        repeat = ui.input("Repeat new password").props("type=password")
        repeat.mark("repeat-password")

        async def change() -> None:
            wanted = str(new.value or "")
            if not wanted:
                ui.notify("Enter a new password")
            elif wanted != repeat.value:
                ui.notify("The new passwords differ")
            elif await save(ctx, request, str(current.value or ""), wanted):
                new.set_value("")
                repeat.set_value("")
                current.set_value("")
                ui.notify("Password changed")

        ui.button("Change password", on_click=change).mark("change-password")
        return None


async def save(ctx: UIContext, request: Request, current: str, new: str) -> bool:
    """Change the password of the account behind the cookie, after checking it."""
    async with ctx.db.async_session_maker() as session:
        user = await ctx.backend.resolve_user(session, request.cookies.get(COOKIE_NAME))
        if user is None:
            ui.notify("Please sign in again")
            return False
        if await authenticate(ctx, session, user.email, current) is None:
            ui.notify("Wrong password")
            return False
        await ctx.backend.user_manager(session).update(UserUpdate(password=new), user)
        return True
