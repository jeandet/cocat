from fastapi import Request
from fastapi.responses import RedirectResponse
from nicegui import ui

from .auth import error_key, login_form, next_query, resend_form
from .context import UIContext
from .layout import current_user, redirect_page, ui_url


def register(ctx: UIContext) -> None:
    @ui.page("/")
    def index(request: Request) -> RedirectResponse:
        return redirect_page(ui_url(request, "/rooms"))

    @ui.page("/login")
    async def login_page(request: Request) -> RedirectResponse | None:
        if await current_user(ctx, request) is not None:
            return redirect_page(ui_url(request, "/rooms"))
        ui.label("Sign in").classes("text-2xl")
        # A relative action with the query carried over, so that a proxy
        # terminating TLS cannot turn the form post into mixed content.
        action = ui_url(request, "/login") + next_query(request)
        ui.html(
            login_form(
                action,
                error_key(request),
                ui_url(request, "/signup"),
                ui_url(request, "/forgot-password"),
            )
        ).mark("login-form")
        ui.html(resend_form(ui_url(request, "/request-verify"))).mark("resend-form")
        for name in ctx.providers:
            ui.link(
                f"Sign in with {name}", ui_url(request, f"/oauth/{name}/login")
            ).mark(f"oauth-{name}")
        return None
