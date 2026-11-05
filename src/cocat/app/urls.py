"""Browser-facing URLs, reverse-proxy prefix included.

Both front ends hand out links, so the prefix arithmetic lives here rather than
in the web UI: `ui_url` for an absolute path inside this app, and `absolute_url`
for a link somebody else will open.
"""

import os
from urllib.parse import urlsplit

from fastapi import Request

PAGES_MOUNT = "/ui"


def ui_url(request: Request, path: str) -> str:
    """Absolute browser path of a page, reverse-proxy prefix included.

    Measured in all four cases: inside a page `root_path` is already `/ui`, and
    `app_root_path` carries the proxy prefix; on a host route there is no
    `app_root_path` and `root_path` is the prefix alone. Guessing this from
    `root_path` alone drops the mount on host routes.
    """
    base = request.scope.get("app_root_path", request.scope.get("root_path", ""))
    return base + PAGES_MOUNT + path


def public_base(request: Request) -> str:
    """Origin other people reach the app at, for the links we hand out.

    A share link is opened from someone else's browser, so its origin is not the
    Host header of the request that created it; a deployment behind a proxy sets
    `COCAT_PUBLIC_URL` instead. A path in that variable would repeat the prefix
    that `ui_url` already adds, so only the origin is kept.
    """
    configured = os.environ.get("COCAT_PUBLIC_URL")
    if configured:
        parts = urlsplit(configured)
        return f"{parts.scheme}://{parts.netloc}"
    return f"{request.url.scheme}://{request.url.netloc}"


def absolute_url(request: Request, path: str) -> str:
    return public_base(request) + ui_url(request, path)
