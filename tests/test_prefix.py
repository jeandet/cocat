from typing import cast

import pytest
from starlette.types import Receive, Scope, Send
from utils import ui_simulation

from cocat.app.app import CocatApp
from cocat.app.prefix import PrefixPath

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    # aiosqlite only runs on asyncio
    return "asyncio"


class Recorder:
    def __init__(self) -> None:
        self.paths: list[str] = []

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.paths.append(scope["path"])


async def recorded(prefix: str, path: str, scope_type: str = "http") -> str:
    recorder = Recorder()
    scope = cast(Scope, {"type": scope_type, "path": path})
    unused = cast(Receive, None)
    await PrefixPath(recorder, prefix)(scope, unused, cast(Send, None))
    assert len(recorder.paths) == 1
    return recorder.paths[0]


async def test_prefix_is_added_when_missing() -> None:
    assert await recorded("/cocat", "/api/rooms") == "/cocat/api/rooms"
    assert await recorded("/cocat", "/", "websocket") == "/cocat/"


async def test_prefix_is_not_duplicated() -> None:
    assert await recorded("/cocat", "/cocat/api/rooms") == "/cocat/api/rooms"
    assert await recorded("/cocat", "/cocat") == "/cocat"


async def test_empty_prefix_is_a_passthrough() -> None:
    assert await recorded("", "/api/rooms") == "/api/rooms"


async def test_pages_work_behind_a_proxy_prefix(
    monkeypatch: pytest.MonkeyPatch, update_dir: str, db_path: str
) -> None:
    monkeypatch.setenv("COCAT_PROXY_PREFIX", "/cocat")

    def app_factory():
        return CocatApp(update_dir, db_path).app

    async with ui_simulation(app_factory) as (_, user):
        client = user.http_client
        stripped = await client.get("/ui/login")
        kept = await client.get("/cocat/ui/login")
        assert stripped.status_code == 200
        assert kept.status_code == 200

        redirect = await client.get("/ui/", follow_redirects=False)
        assert redirect.status_code == 307
        assert redirect.headers["location"] == "/cocat/ui/rooms"
