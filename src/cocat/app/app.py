import os
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from typing import Any

from anyio import CancelScope, sleep_forever
from anyio.abc import TaskStatus
from fastapi import (
    Depends,
    FastAPI,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRoute, APIRouter
from sqlalchemy.ext.asyncio import AsyncSession
from wire_file import AsyncFileClient
from wiredb import AsyncChannel, Room, RoomManager

from ..ui import mount_ui
from ..ui.context import UIContext
from .actions import RoomActions
from .connections import Connections
from .db import DB, Role, User, get_db
from .db import Room as RoomModel
from .mail import Mailer
from .oauth import (
    Provider,
    TrustedEmailClient,
    build_clients,
    providers_from_env,
)
from .prefix import PrefixPath
from .ratelimit import RateLimiter, limit, load_trusted_proxies
from .rooms import RoomError, list_members, list_user_rooms
from .routes import make_api_router, room_error_handler
from .schemas import RoomUsers, UserCreate, UserRead, UserRooms, UserUpdate
from .secret import load_secret
from .storage import remove_room_file, room_file
from .users import get_backend
from .yjs import ReadOnlyChannel


def with_route_dependency(
    router: APIRouter, path: str, dependency: Callable[[Request], None]
) -> APIRouter:
    for route in router.routes:
        if isinstance(route, APIRoute) and route.path == path:
            route.dependencies.append(Depends(dependency))
            return router
    raise ValueError(f"No route {path} on the router")


class StoredRoom(Room):
    def __init__(self, directory: str, db: DB, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._directory = directory
        self._db = db

    async def run(self, *args: Any, **kwargs: Any) -> None:
        await self.task_group.start(self.connect_to_file)
        await super().run(*args, **kwargs)

    async def connect_to_file(self, *, task_status: TaskStatus[None]) -> None:
        try:
            async with AsyncFileClient(
                doc=self.doc,
                path=room_file(self._directory, self.id),
            ):
                task_status.started()
                await sleep_forever()
        finally:
            with CancelScope(shield=True):
                await self.remove_file_if_room_deleted()

    async def remove_file_if_room_deleted(self) -> None:
        async with self._db.async_session_maker() as session:
            if await session.get(RoomModel, self.id) is None:
                remove_room_file(self._directory, self.id)


def make_providers(oauth: Any = None) -> dict[str, Provider]:
    """Read the providers, or take the ones a test hands over."""
    if oauth is None:
        return build_clients(providers_from_env())
    return {
        config.name: Provider(config, TrustedEmailClient(config, client))
        for config, client in oauth
    }


def public_base_from(environ: Mapping[str, str], root_path: str) -> str:
    """Where a provider sends the browser back, proxy prefix included.

    The origin cannot come from the request: the provider redirects the browser
    to an address that must match what was registered with it.
    """
    configured = environ.get("COCAT_PUBLIC_URL", "").strip()
    if not configured:
        raise ValueError("COCAT_PUBLIC_URL is required to build OAuth redirect URLs")
    return configured.rstrip("/") + root_path


class CocatApp:  # pragma: nocover
    def __init__(
        self,
        update_dir: str,
        db_path: str = "./test.db",
        *,
        mail: Mailer | None = None,
        oauth: Any = None,
    ) -> None:
        db = get_db(db_path)
        self.db = db
        self.connections = Connections()
        backend = get_backend(db, load_secret(Path(f"{db_path}.secret")), mail)
        self.mail = mail
        if mail is not None and not os.environ.get("COCAT_PUBLIC_URL", "").strip():
            # A mailed link must not be built from the request's Host header:
            # whoever controls it would receive the token.
            raise ValueError("COCAT_PUBLIC_URL is required when COCAT_SMTP_HOST is set")

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            async with RoomManager(
                partial(StoredRoom, update_dir, db)
            ) as self.room_manager:
                await db.create_db_and_tables()
                yield

        root_path = os.environ.get("COCAT_PROXY_PREFIX", "")
        if root_path:
            root_path = "/" + root_path.strip("/")

        self.app = app = FastAPI(lifespan=lifespan, root_path=root_path)

        app.add_middleware(
            CORSMiddleware,
            allow_origins=[],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        limiter = RateLimiter(10, 60.0)
        check_limit = limit(limiter, load_trusted_proxies())

        auth_router = backend.fastapi_users.get_auth_router(
            backend.auth_backend, requires_verification=mail is not None
        )
        app.include_router(
            with_route_dependency(auth_router, "/login", check_limit),
            prefix="/auth/jwt",
            tags=["auth"],
        )
        register_router = backend.fastapi_users.get_register_router(
            UserRead, UserCreate
        )
        app.include_router(
            with_route_dependency(register_router, "/register", check_limit),
            prefix="/auth",
            tags=["auth"],
        )
        app.include_router(
            with_route_dependency(
                backend.fastapi_users.get_reset_password_router(),
                "/forgot-password",
                check_limit,
            ),
            prefix="/auth",
            tags=["auth"],
        )
        app.include_router(
            with_route_dependency(
                backend.fastapi_users.get_verify_router(UserRead),
                "/request-verify-token",
                check_limit,
            ),
            prefix="/auth",
            tags=["auth"],
        )
        app.include_router(
            backend.fastapi_users.get_users_router(UserRead, UserUpdate),
            prefix="/users",
            tags=["users"],
        )
        app.add_exception_handler(RoomError, room_error_handler)
        app.include_router(
            make_api_router(db, backend, self.connections, update_dir, mail),
            prefix="/api",
        )

        @app.websocket("/room/{room_id}")
        async def connect_room(
            room_id: str,
            authenticated: tuple[WebSocket, User, Role] | None = Depends(
                backend.websocket_auth
            ),
        ):
            if authenticated is None:
                return

            websocket, user, role = authenticated
            await websocket.accept()
            self.connections.add(room_id, user.id, websocket)
            try:
                ywebsocket = YWebSocket(websocket, room_id)
                room = await self.room_manager.get_room(ywebsocket.id)
                channel: AsyncChannel = (
                    ReadOnlyChannel(ywebsocket) if role is Role.viewer else ywebsocket
                )
                await room.serve(channel)
            finally:
                self.connections.discard(room_id, user.id, websocket)

        @app.get("/rooms", response_model=UserRooms)
        async def get_rooms(
            user: User = Depends(backend.current_active_user),
            session: AsyncSession = Depends(db.get_async_session),
        ):
            return {
                "rooms": [
                    room.id for room, _ in await list_user_rooms(session, user.id)
                ]
            }

        @app.get("/room/{room_id}/users", response_model=RoomUsers)
        async def get_room_users(
            room_id: str,
            user: User = Depends(backend.current_active_user),
            session: AsyncSession = Depends(db.get_async_session),
        ):
            members = await list_members(session, room_id, user)
            return {"users": [member.email for member, _ in members]}

        providers = make_providers(oauth)
        public_base = public_base_from(os.environ, root_path) if providers else ""

        mount_ui(
            app,
            UIContext(
                db=db,
                backend=backend,
                actions=RoomActions(self.connections, update_dir, mail),
                check_limit=check_limit,
                providers=providers,
                public_base=public_base,
            ),
        )

        # `add_middleware` prepends, so the last call is outermost and rewrites
        # the path before routing.
        app.add_middleware(PrefixPath, prefix=root_path)


class YWebSocket(AsyncChannel):  # pragma: nocover
    def __init__(self, websocket: WebSocket, path: str) -> None:
        self._websocket = websocket
        self._path = path

    @property
    def id(self) -> str:
        return self._path

    async def __anext__(self):
        try:
            return await self._websocket.receive_bytes()
        except WebSocketDisconnect:
            raise StopAsyncIteration()

    async def send(self, message: bytes) -> None:
        await self._websocket.send_bytes(message)

    async def receive(self) -> bytes:
        return await self._websocket.receive_bytes()
