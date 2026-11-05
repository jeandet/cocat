from fastapi import FastAPI
from nicegui import ui

from ..app.oauth import make_oauth_router as make_api_oauth_router
from ..app.urls import PAGES_MOUNT
from .auth import make_auth_router
from .context import UIContext
from .oauth import make_router as make_web_oauth_router
from .reset import make_reset_router
from .signup import make_signup_router


def mount_ui(app: FastAPI, ctx: UIContext) -> None:
    """Serve the pages on the host app, on the same port and cookie.

    The auth routes go in before the mount: a mount matches any method, so a
    route added after it would never be reached.
    """
    app.include_router(make_auth_router(ctx))
    app.include_router(make_signup_router(ctx))
    app.include_router(make_reset_router(ctx))
    for name, provider in ctx.providers.items():
        app.include_router(
            make_api_oauth_router(
                ctx.backend, provider, f"{ctx.public_base}/auth/{name}/callback"
            ),
            prefix=f"/auth/{name}",
            tags=["auth"],
        )
        app.include_router(
            make_web_oauth_router(
                ctx, provider, f"{ctx.public_base}/ui/oauth/{name}/callback"
            ),
            prefix=f"/ui/oauth/{name}",
        )
    ui.run_with(app, mount_path=PAGES_MOUNT, title="cocat")
    register_pages(ctx)


def register_pages(ctx: UIContext) -> None:
    from .account import register as register_account
    from .confirm_email import register as register_confirm_email
    from .invitation import register as register_invitation
    from .login import register as register_login
    from .reset import register as register_reset
    from .room import register as register_room
    from .rooms import register as register_rooms
    from .signup import register as register_signup
    from .verify import register as register_verify

    register_login(ctx)
    register_reset(ctx)
    register_rooms(ctx)
    register_room(ctx)
    register_invitation(ctx)
    register_account(ctx)
    register_signup(ctx)
    register_verify(ctx)
    register_confirm_email(ctx)
