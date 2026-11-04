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
    from .account import register as register_account
    from .invitation import register as register_invitation
    from .login import register as register_login
    from .room import register as register_room
    from .rooms import register as register_rooms

    register_login(ctx)
    register_rooms(ctx)
    register_room(ctx)
    register_invitation(ctx)
    register_account(ctx)
