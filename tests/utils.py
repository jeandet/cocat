import asyncio
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import TypeVar
from uuid import UUID

import httpx
import requests
from fastapi import FastAPI
from nicegui import core
from nicegui.testing.general import nicegui_reset_globals
from nicegui.testing.user import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cocat.app.db import User as OrmUser
from cocat.app.db import get_db
from cocat.app.rooms import list_user_rooms

COOKIE_NAME = "fastapiusersauth"

T = TypeVar("T")


@dataclass
class Credentials:
    username: str
    password: str


WALLET: dict[str, Credentials] = {}


def wait_for_server(url: str, process: subprocess.Popen, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while True:
        if process.poll() is not None:
            raise RuntimeError(f"Server exited with code {process.returncode}")
        try:
            requests.get(url, timeout=1)
        except requests.exceptions.ConnectionError:
            if time.monotonic() > deadline:
                raise RuntimeError(f"Server did not start within {timeout} s")
            time.sleep(0.1)
        else:
            return


def create_user(email: str, password: str, db_path: str) -> None:
    command = [
        "cocat",
        "create-user",
        "--email",
        email,
        "--password",
        password,
        "--db_path",
        db_path,
    ]
    subprocess.check_call(command)


def add_user_to_room(email: str, room_id: str, db_path: str) -> None:
    command = [
        "cocat",
        "add-user-to-room",
        "--email",
        email,
        "--room_id",
        room_id,
        "--db_path",
        db_path,
    ]
    subprocess.check_call(command)


def create_room(owner: str, room_id: str, db_path: str) -> None:
    command = [
        "cocat",
        "create-room",
        "--owner",
        owner,
        "--room-id",
        room_id,
        "--db_path",
        db_path,
    ]
    subprocess.check_call(command)


@contextmanager
def clean_nicegui() -> Iterator[None]:
    """Reset NiceGUI's globals around a test.

    The reset evicts the modules that own pages (`cocat`, `cocat.ui`, ...), which
    would otherwise give a later test a second `cocat.app.db` and a second `Base`.
    `core.reset()` clears the script client `app.reset()` leaves behind, which
    otherwise makes `ui.run_with` raise `KeyError`.
    """
    saved = {
        name: module
        for name, module in sys.modules.items()
        if name == "cocat" or name.startswith("cocat.")
    }
    try:
        with nicegui_reset_globals():
            core.reset()
            yield
    finally:
        sys.modules.update(saved)


@asynccontextmanager
async def ui_simulation(
    make_app: Callable[[], FastAPI],
) -> AsyncIterator[tuple[FastAPI, User]]:
    """Run a simulated browser against an app whose lifespan is running."""
    os.environ["NICEGUI_USER_SIMULATION"] = "true"
    try:
        with clean_nicegui():
            app = make_app()
            async with app.router.lifespan_context(app):
                transport = httpx.ASGITransport(app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="https://testserver"
                ) as client:
                    yield app, User(client)
    finally:
        os.environ.pop("NICEGUI_USER_SIMULATION", None)


async def log_in(
    user: User, email: str, password: str, next_path: str | None = None
) -> httpx.Response:
    params = {} if next_path is None else {"next": next_path}
    response = await user.http_client.post(
        "/ui/login",
        data={"username": email, "password": password},
        params=params,
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert COOKIE_NAME in response.cookies
    return response


def click(user: User, marker: str) -> None:
    """Click the element carrying `marker`, without waiting for the handler.

    Handlers run as separate tasks, so assertions after a click go through
    `should_see` or `should_notify`, which retry.
    """
    user.find(marker=marker).click()


def fill(user: User, marker: str, text: str) -> None:
    user.find(marker=marker).clear().type(text)


async def should_notify(user: User, message: str, timeout: float = 1.0) -> None:
    """Wait for a notification instead of sleeping a fixed time."""
    deadline = time.monotonic() + timeout
    while message not in user.notify.messages:
        assert time.monotonic() < deadline, f"no {message!r} in {user.notify.messages}"
        await asyncio.sleep(0.01)


async def in_session(db_path: str, work: Callable[[AsyncSession], Awaitable[T]]) -> T:
    """Run `work` against a fresh session, then release the engine."""
    db = get_db(db_path)
    try:
        async with db.async_session_maker() as session:
            return await work(session)
    finally:
        await db.engine.dispose()


async def user_by_email(session: AsyncSession, email: str) -> OrmUser:
    user = await session.scalar(select(OrmUser).filter_by(email=email))
    assert user is not None
    return user


async def user_id(db_path: str, email: str) -> UUID:
    async def work(session: AsyncSession) -> UUID:
        return (await user_by_email(session, email)).id

    return await in_session(db_path, work)


async def rooms_of(db_path: str, email: str) -> dict[str, str]:
    """Room ids by name for the user's rooms."""

    async def work(session: AsyncSession) -> dict[str, str]:
        user = await user_by_email(session, email)
        return {
            room.name: room.id for room, _ in await list_user_rooms(session, user.id)
        }

    return await in_session(db_path, work)


async def set_active(db_path: str, email: str, active: bool) -> None:
    async def work(session: AsyncSession) -> None:
        user = await user_by_email(session, email)
        user.is_active = active
        await session.commit()

    await in_session(db_path, work)


def get_credential(service_name: str, username: str | None) -> Credentials | None:
    return WALLET.get(service_name)


def set_password(service_name: str, username: str, password: str) -> None:
    WALLET[service_name] = Credentials(username, password)
