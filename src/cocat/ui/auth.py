import html
from urllib.parse import urlencode, urlsplit

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from fastapi.security import OAuth2PasswordRequestForm

from .context import UIContext
from .layout import ui_url

MESSAGES = {
    "credentials": "Wrong email or password.",
    "too_many": "Too many attempts. Try again in a minute.",
}


def same_site(request: Request) -> None:
    """Refuse a form post that another site started.

    Missing headers pass: that is a test client or an old browser, and the login
    cookie is `SameSite=Lax` besides.
    """
    origin = request.headers.get("origin")
    if origin is not None and urlsplit(origin).netloc != request.headers.get("host"):
        raise HTTPException(status.HTTP_403_FORBIDDEN)
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None and fetch_site not in ("same-origin", "same-site"):
        raise HTTPException(status.HTTP_403_FORBIDDEN)


def safe_next(request: Request, value: str | None) -> str:
    """Only follow a `next` that stays on this site."""
    default = ui_url(request, "/rooms")
    if value is None:
        return default
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or "\\" in value:
        return default
    return value if parts.path.startswith("/") else default


def login_form(action: str, error: str | None) -> str:
    message = MESSAGES.get(error or "", "")
    return f"""
<form method="post" action="{html.escape(action)}">
  <label for="username">Email</label>
  <input id="username" name="username" type="email" autocomplete="username" required>
  <label for="password">Password</label>
  <input id="password" name="password" type="password"
         autocomplete="current-password" required>
  <button type="submit">Log in</button>
  <p class="error">{html.escape(message)}</p>
</form>
<p>Accounts are created by an administrator.</p>
"""


def logout_form(action: str) -> str:
    return (
        f'<form method="post" action="{html.escape(action)}">'
        '<button type="submit">Log out</button></form>'
    )


def redirect(target: str) -> RedirectResponse:
    return RedirectResponse(target, status.HTTP_303_SEE_OTHER)


def with_cookies(response: Response, source: Response) -> Response:
    for header in source.headers.getlist("set-cookie"):
        response.headers.append("set-cookie", header)
    return response


def make_auth_router(ctx: UIContext) -> APIRouter:
    """Log in and out, as plain routes rather than pages.

    A password belongs in a body and clearing a cookie needs a response, which
    is what a page handler cannot give; the rest of the UI calls the services
    directly and needs no route of its own. The routes are registered before the
    `/ui` mount, which matches any method: a `POST /ui/login` that exists first
    keeps `GET /ui/login` reaching the page.
    """
    router = APIRouter()
    transport = ctx.backend.auth_backend.transport

    @router.post("/ui/login")
    async def post_login(request: Request) -> Response:
        same_site(request)
        try:
            ctx.check_limit(request)
        except HTTPException:
            return redirect(ui_url(request, "/login") + "?error=too_many")

        form = await request.form()
        credentials = OAuth2PasswordRequestForm(
            grant_type="password",
            username=str(form.get("username", "")),
            password=str(form.get("password", "")),
        )
        async with ctx.db.async_session_maker() as session:
            user = await ctx.backend.user_manager(session).authenticate(credentials)
            if user is None or not user.is_active:
                return redirect(ui_url(request, "/login") + "?error=credentials")
            token = await ctx.backend.get_jwt_strategy().write_token(user)

        response = redirect(safe_next(request, request.query_params.get("next")))
        return with_cookies(response, await transport.get_login_response(token))

    @router.post("/ui/logout")
    async def post_logout(request: Request) -> Response:
        same_site(request)
        response = redirect(ui_url(request, "/login"))
        return with_cookies(response, await transport.get_logout_response())

    return router


def next_query(request: Request) -> str:
    target = request.query_params.get("next")
    return "" if target is None else "?" + urlencode({"next": target})


def error_key(request: Request) -> str | None:
    return request.query_params.get("error")
