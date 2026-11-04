import uuid
from dataclasses import dataclass
from typing import Annotated, Any, cast

from fastapi import Cookie, Depends, Path, Request, WebSocket, status
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, models
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.db import BaseUserDatabase, SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from .db import DB, User
from .rooms import create_personal_room, get_role


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    async def on_after_register(
        self, user: User, request: Request | None = None
    ) -> None:
        await create_personal_room(user_db_session(self.user_db), user)
        print(f"User {user.id} has registered.")

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        print(f"User {user.id} requested a password reset.")

    async def on_after_request_verify(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        print(f"User {user.id} requested a verification.")


def make_user_manager(user_db: SQLAlchemyUserDatabase, secret: str) -> UserManager:
    user_manager = UserManager(user_db)
    user_manager.reset_password_token_secret = secret
    user_manager.verification_token_secret = secret
    return user_manager


def user_db_session(user_db: BaseUserDatabase[User, uuid.UUID]) -> AsyncSession:
    return cast(SQLAlchemyUserDatabase, user_db).session


async def user_from_token(
    session: AsyncSession,
    token: str | None,
    user_manager: UserManager,
    strategy: JWTStrategy[models.UP, models.ID],
) -> User | None:
    """Return the active user a JWT belongs to, or None.

    A missing, unparsable or expired token reads as None rather than raising, so
    callers only have to decide what an anonymous request means.
    """
    if token is None:
        return None
    user = await strategy.read_token(token, cast(Any, user_manager))  # type: ignore[func-returns-value]
    active = user is not None and user.is_active
    return cast(User, user) if active else None


@dataclass
class Backend:
    secret: str
    auth_backend: Any
    current_active_user: Any
    fastapi_users: Any
    get_jwt_strategy: Any
    get_user_manager: Any
    websocket_auth: Any

    def user_manager(self, session: AsyncSession) -> UserManager:
        return make_user_manager(SQLAlchemyUserDatabase(session, User), self.secret)

    async def resolve_user(
        self, session: AsyncSession, token: str | None
    ) -> User | None:
        return await user_from_token(
            session, token, self.user_manager(session), self.get_jwt_strategy()
        )


def get_backend(db: DB, secret: str) -> Backend:
    async def get_user_manager(
        user_db: SQLAlchemyUserDatabase = Depends(db.get_user_db),
    ):
        yield make_user_manager(user_db, secret)

    async def websocket_auth(
        websocket: WebSocket,
        room_id: str = Path(...),
        fastapiusersauth: Annotated[str | None, Cookie()] = None,
    ) -> tuple[WebSocket, User] | None:
        async with db.async_session_maker() as session:
            user = await user_from_token(
                session,
                fastapiusersauth,
                make_user_manager(SQLAlchemyUserDatabase(session, User), secret),
                get_jwt_strategy(),
            )
            if (
                user is not None
                and await get_role(session, room_id, user.id) is not None
            ):
                return websocket, user

        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return None

    def get_jwt_strategy() -> JWTStrategy[models.UP, models.ID]:
        return JWTStrategy(secret=secret, lifetime_seconds=3600)

    auth_backend: AuthenticationBackend = AuthenticationBackend(
        name="cookie",
        transport=CookieTransport(),
        get_strategy=get_jwt_strategy,
    )

    fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

    current_active_user = fastapi_users.current_user(active=True)

    return Backend(
        secret=secret,
        auth_backend=auth_backend,
        current_active_user=current_active_user,
        fastapi_users=fastapi_users,
        get_jwt_strategy=get_jwt_strategy,
        get_user_manager=get_user_manager,
        websocket_auth=websocket_auth,
    )
