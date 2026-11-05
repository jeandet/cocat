"""The sign-up page, and the form route that creates an account."""

import html

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi_users.exceptions import UserAlreadyExists
from nicegui import ui
from pydantic import ValidationError

from ..app.mail import MailError
from ..app.schemas import UserCreate
from .auth import MESSAGES, error_key, redirect, same_site, with_cookies
from .context import UIContext
from .layout import ui_url


def signup_form(action: str, message: str) -> str:
    return f"""
<form method="post" action="{html.escape(action)}">
  <label for="email">Email</label>
  <input id="email" name="email" type="email" autocomplete="email" required>
  <label for="password">Password</label>
  <input id="password" name="password" type="password"
         autocomplete="new-password" required>
  <button id="signup-submit" type="submit">Sign up</button>
  <p class="error">{html.escape(message)}</p>
</form>
<p>Already have an account? <a href="{html.escape(action.rsplit("/", 1)[0])}/login">Log in</a></p>
"""


def make_signup_router(ctx: UIContext) -> APIRouter:
    """The page cannot set a cookie, so the form posts to a plain route."""
    router = APIRouter()

    @router.post("/ui/signup")
    async def post_signup(request: Request) -> Response:
        same_site(request)
        try:
            ctx.check_limit(request)
        except HTTPException:
            return redirect(ui_url(request, "/signup") + "?error=too_many")
        form = await request.form()
        try:
            credentials = UserCreate(
                email=str(form.get("email", "")),
                password=str(form.get("password", "")),
            )
        except ValidationError:
            return redirect(ui_url(request, "/signup") + "?error=email")
        async with ctx.db.async_session_maker() as session:
            manager = ctx.backend.user_manager(session)
            try:
                user = await manager.create(credentials, request=request)
            except UserAlreadyExists:
                return redirect(ui_url(request, "/signup") + "?error=exists")
            except MailError:
                return redirect(ui_url(request, "/login") + "?error=mail")
            if ctx.backend.mail is not None:
                return redirect(ui_url(request, "/login") + "?created=1")
            token = await ctx.backend.get_jwt_strategy().write_token(user)
            response = redirect(ui_url(request, "/rooms"))
            return with_cookies(
                response,
                await ctx.backend.auth_backend.transport.get_login_response(token),
            )

    return router


def register(ctx: UIContext) -> None:
    @ui.page("/signup")
    def signup_page(request: Request) -> None:
        message = MESSAGES.get(error_key(request) or "", "")
        ui.label("Sign up").classes("text-2xl")
        ui.html(signup_form(ui_url(request, "/signup"), message)).mark("signup-form")
