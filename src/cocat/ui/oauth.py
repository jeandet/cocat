"""Browser side of the OAuth dance.

The stock fastapi-users callback answers a browser with a bare 204, which
leaves it on a blank page. These routes do the same checks and finish with a
redirect, setting the same login cookie.
"""

import secrets

import jwt
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi_users.exceptions import UserAlreadyExists
from fastapi_users.jwt import decode_jwt
from fastapi_users.router.oauth import (
    CSRF_TOKEN_COOKIE_NAME,
    CSRF_TOKEN_KEY,
    STATE_TOKEN_AUDIENCE,
    generate_csrf_token,
    generate_state_token,
)
from httpx_oauth.oauth2 import GetAccessTokenError

from ..app.mail import is_placeholder
from ..app.oauth import Provider, placeholder_email
from .auth import redirect, with_cookies
from .context import UIContext
from .layout import ui_url

STATE_LIFETIME_SECONDS = 3600


def make_router(ctx: UIContext, provider: Provider, callback_url: str) -> APIRouter:
    name = provider.config.name
    router = APIRouter()

    @router.get("/login")
    async def login() -> Response:
        csrf_token = generate_csrf_token()
        state = generate_state_token({CSRF_TOKEN_KEY: csrf_token}, ctx.backend.secret)
        authorization_url = await provider.client.get_authorization_url(
            redirect_uri=callback_url,
            state=state,
            scope=provider.config.scopes,
        )
        response = RedirectResponse(
            authorization_url, status.HTTP_307_TEMPORARY_REDIRECT
        )
        response.set_cookie(
            CSRF_TOKEN_COOKIE_NAME,
            csrf_token,
            max_age=STATE_LIFETIME_SECONDS,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.get("/callback")
    async def callback(
        request: Request,
        code: str | None = Query(None),
        state: str | None = Query(None),
    ) -> Response:
        if code is None or state is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing code or state")
        if not _trusted_state(ctx, request, state):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad state")
        try:
            token = await provider.client.get_access_token(code, callback_url)
        except GetAccessTokenError as error:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Could not reach the provider"
            ) from error
        account_id, claimed = await provider.client.get_id_email(token["access_token"])
        account_email = claimed or placeholder_email(name, account_id)
        async with ctx.db.async_session_maker() as session:
            manager = ctx.backend.user_manager(session)
            try:
                user = await manager.oauth_callback(
                    name,
                    token["access_token"],
                    account_id,
                    account_email,
                    expires_at=token.get("expires_at"),
                    refresh_token=token.get("refresh_token"),
                    request=request,
                    associate_by_email=provider.config.verifies_email
                    and ctx.backend.mail is not None,
                    is_verified_by_default=provider.config.verifies_email,
                )
            except UserAlreadyExists:
                return redirect(ui_url(request, "/login") + "?error=exists")
            if not user.is_active:
                return redirect(ui_url(request, "/login") + "?error=credentials")
            target = "/confirm-email" if is_placeholder(user.email) else "/rooms"
            login_token = await ctx.backend.get_jwt_strategy().write_token(user)
            response = redirect(ui_url(request, target))
            return with_cookies(
                response,
                await ctx.backend.auth_backend.transport.get_login_response(
                    login_token
                ),
            )

    return router


def _trusted_state(ctx: UIContext, request: Request, state: str) -> bool:
    """The state must be ours and match the cookie this browser was given."""
    try:
        claims = decode_jwt(state, ctx.backend.secret, [STATE_TOKEN_AUDIENCE])
    except jwt.InvalidTokenError:  # expired, forged, wrong audience, garbled
        return False
    cookie = request.cookies.get(CSRF_TOKEN_COOKIE_NAME)
    return cookie is not None and secrets.compare_digest(
        str(claims.get(CSRF_TOKEN_KEY, "")), cookie
    )
