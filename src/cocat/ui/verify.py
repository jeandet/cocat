"""The page a verification link lands on."""

from fastapi import Request
from fastapi_users.exceptions import InvalidVerifyToken, UserAlreadyVerified
from nicegui import ui

from .context import UIContext
from .layout import ui_url
from .token import CLEAR_FRAGMENT_SCRIPT, read_fragment_token

VERIFIED = "Your address is verified."
ALREADY = "Your address is already verified."
INVALID = "This verification link is not valid anymore."
MISSING = "Open the link from your inbox to verify your address."


def register(ctx: UIContext) -> None:
    @ui.page("/verify")
    async def verify_page(request: Request) -> None:
        ui.label("Verify your address").classes("text-2xl")
        token = await read_fragment_token()
        if token:
            ui.run_javascript(CLEAR_FRAGMENT_SCRIPT)
        message = await verify(ctx, request, token) if token else MISSING
        ui.label(message)
        ui.link("My rooms", ui_url(request, "/rooms")).mark("nav-rooms")


async def verify(ctx: UIContext, request: Request, token: str) -> str:
    """Spend the token, and say what happened in a sentence the page can show."""
    async with ctx.db.async_session_maker() as session:
        manager = ctx.backend.user_manager(session)
        try:
            await manager.verify(token, request)
        except InvalidVerifyToken:
            return INVALID
        except UserAlreadyVerified:
            return ALREADY
        return VERIFIED
