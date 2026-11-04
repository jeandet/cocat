from fastapi import Request

from ..app.db import User
from .context import PAGES_MOUNT, UIContext

COOKIE_NAME = "fastapiusersauth"


def ui_url(request: Request, path: str) -> str:
    """Absolute browser path of a UI page, reverse-proxy prefix included.

    Measured in all four cases: inside a page `root_path` is already `/ui`, and
    `app_root_path` carries the proxy prefix; on a host route there is no
    `app_root_path` and `root_path` is the prefix alone. Guessing this from
    `root_path` alone drops the mount on host routes.
    """
    base = request.scope.get("app_root_path", request.scope.get("root_path", ""))
    return base + PAGES_MOUNT + path


async def current_user(ctx: UIContext, request: Request) -> User | None:
    token = request.cookies.get(COOKIE_NAME)
    async with ctx.db.async_session_maker() as session:
        return await ctx.backend.resolve_user(session, token)
