import os
import signal
import subprocess
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from uuid import uuid4

import anyio
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from nicegui.testing.user import User as SimulatedUser
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect
from utils import (
    FakeOAuthClient,
    RecordingMail,
    clean_nicegui,
    create_room,
    create_user,
    log_in,
    make_app,
    oauth_provider,
    ui_simulation,
    wait_for_server,
)

from cocat.app.app import CocatApp
from cocat.app.db import User, get_db
from cocat.app.oauth import OAuthProviderConfig
from cocat.cli import _create_user


async def make_orm_user(session: AsyncSession, email: str) -> User:
    user = User(
        email=email,
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=True,
    )
    session.add(user)
    await session.commit()
    return user


def assert_websocket_refused(
    client: TestClient, url: str, headers: dict[str, str] | None = None
) -> None:
    with pytest.raises(WebSocketDisconnect) as refused:
        client.websocket_connect(url, headers=headers).__enter__()
    assert refused.value.code == 1008


@pytest.fixture()
async def session(tmp_path):
    db = get_db(str(tmp_path / "u.db"))
    await db.create_db_and_tables()
    async with db.async_session_maker() as session:
        yield session


@dataclass
class AppClient:
    client: TestClient
    db_path: str
    update_dir: str


@dataclass
class MailClient:
    client: TestClient
    mail: RecordingMail
    db_path: str
    update_dir: str


@pytest.fixture()
def app_client(update_dir: str, db_path: str) -> Iterator[AppClient]:
    with clean_nicegui():
        app = make_app(update_dir, db_path)
        with TestClient(app, base_url="https://testserver") as client:
            yield AppClient(client=client, db_path=db_path, update_dir=update_dir)


@pytest.fixture()
def app_client_mail(update_dir: str, db_path: str) -> Iterator[MailClient]:
    mail = RecordingMail()
    with clean_nicegui():
        app = make_app(update_dir, db_path, mail)
        with TestClient(app, base_url="https://testserver") as client:
            yield MailClient(
                client=client, mail=mail, db_path=db_path, update_dir=update_dir
            )


@dataclass
class UI:
    user: SimulatedUser
    cocat: CocatApp
    db_path: str
    update_dir: str
    mail: RecordingMail | None = None
    oauth_client: FakeOAuthClient | None = None
    email: str = "ui@test.org"
    password: str = "pwd"

    async def log_in(self) -> httpx.Response:
        return await log_in(self.user, self.email, self.password)


@asynccontextmanager
async def _simulated_ui(
    update_dir: str,
    db_path: str,
    mail: RecordingMail | None = None,
    oauth: list[tuple[OAuthProviderConfig, FakeOAuthClient]] | None = None,
) -> AsyncIterator[UI]:
    await _create_user(UI.email, UI.password, False, db_path)
    built: list[CocatApp] = []

    def make() -> FastAPI:
        built.append(CocatApp(update_dir, db_path, mail=mail, oauth=oauth))
        return built[-1].app

    async with ui_simulation(make) as (_, user):
        yield UI(
            user=user,
            cocat=built[-1],
            db_path=db_path,
            update_dir=update_dir,
            mail=mail,
            oauth_client=None if oauth is None else oauth[0][1],
        )


@pytest.fixture()
async def ui(update_dir: str, db_path: str) -> AsyncIterator[UI]:
    async with _simulated_ui(update_dir, db_path) as simulated:
        yield simulated


def make_user(db_path: str, email: str, password: str = "pwd") -> None:
    anyio.run(_create_user, email, password, False, db_path)


def login(client: TestClient, email: str, password: str = "pwd") -> None:
    response = client.post(
        "/auth/jwt/login", data={"username": email, "password": password}
    )
    assert response.status_code == 204


def ws_headers(client: TestClient) -> dict[str, str]:
    """Headers to send a login cookie over a websocket.

    `TestClient.websocket_connect` always uses `ws://`, so httpx would not send the
    `Secure` cookie by itself.
    """
    return {"cookie": f"fastapiusersauth={client.cookies['fastapiusersauth']}"}


@pytest.fixture()
def update_dir(tmp_path):
    update_dir = tmp_path / "update_dir"
    update_dir.mkdir()
    yield str(update_dir)


@pytest.fixture()
def db_path(tmp_path):
    yield str(tmp_path / "test.db")


@pytest.fixture()
def room_id():
    return str(uuid4())


@pytest.fixture()
def user(db_path: str, room_id: str):
    email = f"user_{uuid4().hex}@foo.com"
    password = f"pwd_{uuid4().hex}"
    create_user(email, password, db_path)
    create_room(email, room_id, db_path)
    yield email, password


@pytest.fixture()
def server(free_tcp_port: int, update_dir: str, db_path: str):
    host = "127.0.0.1"
    command = [
        "cocat",
        "serve",
        "--host",
        host,
        "--port",
        str(free_tcp_port),
        "--update_dir",
        update_dir,
        "--db_path",
        db_path,
    ]
    p = subprocess.Popen(command)
    url = f"http://{host}:{free_tcp_port}"
    wait_for_server(url, p)
    yield host, free_tcp_port
    os.kill(p.pid, signal.SIGINT)
    p.wait()


@pytest.fixture()
async def ui_oauth(
    update_dir: str, db_path: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[UI]:
    monkeypatch.setenv("COCAT_PUBLIC_URL", "https://testserver")
    client = FakeOAuthClient()
    config = oauth_provider(verifies_email=True)
    async with _simulated_ui(
        update_dir, db_path, oauth=[(config, client)]
    ) as simulated:
        yield simulated


@pytest.fixture()
async def ui_oauth_placeholder(
    update_dir: str, db_path: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[UI]:
    monkeypatch.setenv("COCAT_PUBLIC_URL", "https://testserver")
    client = FakeOAuthClient(email=None)
    config = oauth_provider()
    async with _simulated_ui(
        update_dir, db_path, oauth=[(config, client)]
    ) as simulated:
        yield simulated


@pytest.fixture()
async def ui_mail(update_dir: str, db_path: str) -> AsyncIterator[UI]:
    async with _simulated_ui(update_dir, db_path, RecordingMail()) as simulated:
        yield simulated
