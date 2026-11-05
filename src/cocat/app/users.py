import logging
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
from fastapi_users.exceptions import UserAlreadyExists, UserNotExists
from sqlalchemy.ext.asyncio import AsyncSession

from .db import DB, OAuthAccount, User
from .mail import Mailer, MailError, is_placeholder, reset_mail, verification_mail
from .rooms import create_personal_room, get_role

LOGGER = logging.getLogger(__name__)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    def __init__(
        self, user_db: BaseUserDatabase[User, uuid.UUID], mail: Mailer | None = None
    ) -> None:
        super().__init__(user_db)
        self.mail = mail

    async def on_after_register(
        self, user: User, request: Request | None = None
    ) -> None:
        await create_personal_room(user_db_session(self.user_db), user)
        await self.verify_account(user, request)

    async def verify_account(self, user: User, request: Request | None = None) -> None:
        """Ask the user to confirm the address, unless that is already settled.

        The REST register route and the UI sign-up page both reach it through
        `on_after_register`.
        """
        if user.is_verified:
            return
        from .accounts import start_verification  # accounts builds on this module

        await start_verification(self, user, request)

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        if self.mail is None or request is None:
            return
        from .accounts import reset_link  # accounts builds on this module

        # simplify: sent inline, so a slow relay shows up as a slow response and
        # the latency still hints whether the address exists (rate limited).
        # Upgrade: send from a background task once there is a task group here.
        await self._mail(user, *reset_mail(reset_link(request, token)))

    async def on_after_request_verify(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        if self.mail is None or request is None:
            return
        from .accounts import verification_link  # accounts builds on this module

        await self._mail(user, *verification_mail(verification_link(request, token)))

    async def _mail(self, user: User, subject: str, body: str) -> None:
        """Best effort: a refused message must not fail the caller's request.

        The REST register, reset and verify routes then answer the same way
        whether or not the relay is up, so their replies cannot probe accounts.
        """
        assert self.mail is not None
        try:
            await self.mail.send(user.email, subject, body)
        except MailError:
            LOGGER.warning("Could not mail user %s", user.id)

    async def oauth_callback(
        self,
        oauth_name: str,
        access_token: str,
        account_id: str,
        account_email: str,
        expires_at: int | None = None,
        refresh_token: str | None = None,
        request: Request | None = None,
        *,
        associate_by_email: bool = False,
        is_verified_by_default: bool = False,
    ) -> User:
        """Link an OAuth identity, but only to an account that proved its address.

        Associating by email is an account takeover waiting to happen when the
        local account never proved it owns the address: anybody who registered
        it first would inherit the identity. A provider that does not hand over
        a usable address gets a placeholder, which never matches an account, and
        a new account is only marked verified when the operator vouched for the
        provider (`is_verified_by_default` is forced off for placeholders).
        """
        associate_by_email = associate_by_email and _real_address(account_email)
        if associate_by_email:
            existing = await self._existing(account_email)
            if existing is not None and not existing.is_verified:
                raise UserAlreadyExists()
            associate_by_email = existing is not None
        return await super().oauth_callback(
            oauth_name,
            access_token,
            account_id,
            account_email,
            expires_at,
            refresh_token,
            request,
            associate_by_email=associate_by_email,
            is_verified_by_default=is_verified_by_default
            and not is_placeholder(account_email),
        )

    async def _existing(self, email: str) -> User | None:
        try:
            return await self.get_by_email(email)
        except UserNotExists:
            return None


def _real_address(email: str) -> bool:
    return not is_placeholder(email)


def make_user_manager(
    user_db: SQLAlchemyUserDatabase, secret: str, mail: Mailer | None = None
) -> UserManager:
    user_manager = UserManager(user_db, mail)
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
    mail: Mailer | None
    auth_backend: Any
    current_active_user: Any
    fastapi_users: Any
    get_jwt_strategy: Any
    get_user_manager: Any
    websocket_auth: Any

    def user_manager(self, session: AsyncSession) -> UserManager:
        return make_user_manager(
            SQLAlchemyUserDatabase(session, User, OAuthAccount), self.secret, self.mail
        )

    async def resolve_user(
        self, session: AsyncSession, token: str | None
    ) -> User | None:
        return await user_from_token(
            session, token, self.user_manager(session), self.get_jwt_strategy()
        )


def get_backend(db: DB, secret: str, mail: Mailer | None = None) -> Backend:
    async def get_user_manager(
        user_db: SQLAlchemyUserDatabase = Depends(db.get_user_db),
    ):
        yield make_user_manager(user_db, secret, mail)

    async def websocket_auth(
        websocket: WebSocket,
        room_id: str = Path(...),
        fastapiusersauth: Annotated[str | None, Cookie()] = None,
    ) -> tuple[WebSocket, User] | None:
        async with db.async_session_maker() as session:
            user = await user_from_token(
                session,
                fastapiusersauth,
                make_user_manager(
                    SQLAlchemyUserDatabase(session, User, OAuthAccount), secret, mail
                ),
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
        mail=mail,
        auth_backend=auth_backend,
        current_active_user=current_active_user,
        fastapi_users=fastapi_users,
        get_jwt_strategy=get_jwt_strategy,
        get_user_manager=get_user_manager,
        websocket_auth=websocket_auth,
    )
