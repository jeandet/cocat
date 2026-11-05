"""Forgot-password and reset pages."""

import html

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi_users.exceptions import (
    InvalidResetPasswordToken,
    UserInactive,
    UserNotExists,
)
from nicegui import ui

from .auth import MESSAGES, error_key, redirect, same_site
from .context import UIContext
from .layout import ui_url
from .token import CLEAR_FRAGMENT_SCRIPT, read_fragment_token

TITLE = "Forgot password"
RESET_TITLE = "Choose a new password"
EMPTY = "Enter a new password"
MISMATCH = "The new passwords differ"
INVALID = "This reset link is not valid anymore."


def forgot_form(action: str, message: str) -> str:
    return f"""
<form method="post" action="{html.escape(action)}">
  <label for="email">Email</label>
  <input id="email" name="email" type="email" autocomplete="email" required>
  <button id="forgot-submit" type="submit">Send a reset link</button>
  <p class="error">{html.escape(message)}</p>
</form>
"""


def make_reset_router(ctx: UIContext) -> APIRouter:
    """The form posts here because a page cannot answer with a redirect."""
    router = APIRouter()

    @router.post("/ui/forgot-password")
    async def post_forgot(request: Request) -> Response:
        same_site(request)
        try:
            ctx.check_limit(request)
        except HTTPException:
            return redirect(ui_url(request, "/forgot-password") + "?error=too_many")
        form = await request.form()
        async with ctx.db.async_session_maker() as session:
            manager = ctx.backend.user_manager(session)
            try:
                user = await manager.get_by_email(str(form.get("email", "")))
            except UserNotExists:
                # The same answer either way, so the form cannot be used to
                # find out which addresses have an account.
                pass
            else:
                if user.is_active:
                    await manager.forgot_password(user, request)
        return redirect(ui_url(request, "/forgot-password") + "?sent=1")

    return router


async def apply(ctx: UIContext, request: Request, token: str, password: str) -> bool:
    async with ctx.db.async_session_maker() as session:
        manager = ctx.backend.user_manager(session)
        try:
            await manager.reset_password(token, password, request)
        except (InvalidResetPasswordToken, UserInactive, UserNotExists):
            ui.notify(INVALID)
            return False
    return True


def register(ctx: UIContext) -> None:
    @ui.page("/forgot-password")
    def forgot_page(request: Request) -> None:
        ui.label(TITLE).classes("text-2xl")
        message = MESSAGES.get(error_key(request) or "", "")
        ui.html(forgot_form(ui_url(request, "/forgot-password"), message)).mark(
            "forgot-form"
        )

    @ui.page("/reset-password")
    async def reset_page(request: Request) -> None:
        ui.label(RESET_TITLE).classes("text-2xl")
        token = await read_fragment_token()
        if token:
            ui.run_javascript(CLEAR_FRAGMENT_SCRIPT)
        password = ui.input("New password").props("type=password")
        password.mark("new-password")
        repeat = ui.input("Repeat password").props("type=password")
        repeat.mark("repeat-password")

        async def submit() -> None:
            wanted = password.value or ""
            if not wanted:
                ui.notify(EMPTY)
                return
            if wanted != (repeat.value or ""):
                ui.notify(MISMATCH)
                return
            if await apply(ctx, request, token, wanted):
                password.set_value("")
                repeat.set_value("")
                ui.notify("Password changed")
                ui.navigate.to(ui_url(request, "/login") + "?reset=1")

        ui.button("Change password", on_click=submit).mark("reset-submit")
