"""Account lifecycle steps shared by the REST API and the web UI."""

from fastapi import Request

from .db import User
from .mail import is_placeholder
from .schemas import UserUpdate
from .urls import absolute_url
from .users import UserManager


def verification_link(request: Request, token: str) -> str:
    return absolute_url(request, f"/verify#{token}")


def reset_link(request: Request, token: str) -> str:
    return absolute_url(request, f"/reset-password#{token}")


async def start_verification(
    manager: UserManager, user: User, request: Request | None
) -> None:
    """Ask the user to confirm the address, or skip it when nothing can be mailed.

    A deployment without SMTP cannot deliver anything, so requiring a
    confirmation would lock everyone out of the account they just created.
    """
    if is_placeholder(user.email):
        return
    if manager.mail is None:
        await manager.update(UserUpdate(is_verified=True), user)
    elif request is not None:
        # Without a request there is no link to mail, so nothing happens here.
        await manager.request_verify(user, request)
