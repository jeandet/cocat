from fastapi import FastAPI
from nicegui import ui

from .auth import make_auth_router
from .context import PAGES_MOUNT, UIContext


def mount_ui(app: FastAPI, ctx: UIContext) -> None:
    """Serve the pages on the host app, on the same port and cookie.

    The auth routes go in before the mount: a mount matches any method, so a
    route added after it would never be reached.
    """
    app.include_router(make_auth_router(ctx))
    ui.run_with(app, mount_path=PAGES_MOUNT, title="cocat")
    register_pages(ctx)


def register_pages(ctx: UIContext) -> None:
    from .login import register

    register(ctx)
