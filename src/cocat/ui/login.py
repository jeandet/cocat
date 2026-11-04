from fastapi import Request, status
from fastapi.responses import RedirectResponse
from nicegui import ui

from .auth import error_key, login_form, next_query
from .context import UIContext
from .layout import current_user, ui_url


def register(ctx: UIContext) -> None:
    @ui.page("/")
    def index(request: Request) -> RedirectResponse:
        return RedirectResponse(
            ui_url(request, "/rooms"), status.HTTP_307_TEMPORARY_REDIRECT
        )

    @ui.page("/login")
    async def login_page(request: Request) -> RedirectResponse | None:
        if await current_user(ctx, request) is not None:
            return RedirectResponse(
                ui_url(request, "/rooms"), status.HTTP_307_TEMPORARY_REDIRECT
            )
        ui.label("Sign in").classes("text-2xl")
        # A relative action with the query carried over, so that a proxy
        # terminating TLS cannot turn the form post into mixed content.
        action = ui_url(request, "/login") + next_query(request)
        ui.html(login_form(action, error_key(request)))
        return None
