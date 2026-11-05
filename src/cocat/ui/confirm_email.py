from fastapi import Request
from fastapi.responses import RedirectResponse
from fastapi_users.exceptions import UserAlreadyExists
from nicegui import ui
from pydantic import ValidationError

from ..app.accounts import start_verification
from ..app.mail import is_placeholder
from ..app.schemas import UserUpdate
from .context import UIContext
from .layout import COOKIE_NAME, page_header, redirect_page, require_user, ui_url

TITLE = "Confirm your email"


def register(ctx: UIContext) -> None:
    @ui.page("/confirm-email")
    async def confirm_email_page(request: Request) -> RedirectResponse | None:
        user = await require_user(ctx, request)
        if isinstance(user, RedirectResponse):
            return user
        if not is_placeholder(user.email):
            # Only an account without an address of its own needs this page;
            # changing a proven one is the account page's business.
            return redirect_page(ui_url(request, "/account"))
        page_header(request, user)
        ui.label(TITLE).classes("text-2xl")
        address = ui.input("Email address")
        address.mark("confirm-email")
        ui.button(
            "Save", on_click=lambda: save(ctx, request, str(address.value or ""))
        ).mark("confirm-email-send")
        return None


async def save(ctx: UIContext, request: Request, address: str) -> None:
    """Give an account without a usable address one, then ask to confirm it.

    A provider may withhold the address entirely, in which case the account
    carries a placeholder until the user supplies one here.
    """
    wanted = address.strip()
    if not wanted or is_placeholder(wanted):
        ui.notify("Enter your email address")
        return
    try:
        update = UserUpdate(email=wanted)
    except ValidationError:
        ui.notify("That is not an email address")
        return
    async with ctx.db.async_session_maker() as session:
        user = await ctx.backend.resolve_user(session, request.cookies.get(COOKIE_NAME))
        if user is None:
            ui.notify("Please sign in again")
            return
        manager = ctx.backend.user_manager(session)
        try:
            await manager.update(update, user)
        except UserAlreadyExists:
            ui.notify(
                "That address belongs to an existing account: sign in with it and "
                "reset its password if you do not have one"
            )
            return
        await start_verification(manager, user, request)
    if ctx.backend.mail is None:
        ui.notify("Your address is confirmed.")
    else:
        ui.notify(f"Check your inbox to verify {wanted}.")
