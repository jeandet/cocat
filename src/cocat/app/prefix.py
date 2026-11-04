from typing import Any

from starlette.types import Receive, Scope, Send


class PrefixPath:
    """Put the reverse-proxy prefix back where the mounted apps expect it.

    A proxy that strips `/cocat` from incoming requests leaves `path="/ui/rooms"`
    in the scope, while the mounted app is routed against `root_path="/cocat/ui"`
    and therefore 404s. Restoring the prefix makes both agree. Plain routes never
    noticed the missing prefix, which is why the REST API worked without this.
    """

    def __init__(self, app: Any, prefix: str) -> None:
        self.app = app
        stripped = prefix.strip("/")
        self.prefix = "/" + stripped if stripped else ""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if self.prefix and path.startswith("/") and not self._prefixed(path):
            scope = dict(scope)  # type: ignore[assignment]
            scope["path"] = self.prefix + path
        await self.app(scope, receive, send)

    def _prefixed(self, path: str) -> bool:
        return path == self.prefix or path.startswith(self.prefix + "/")
