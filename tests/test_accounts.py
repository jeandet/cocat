from typing import Any, cast

import pytest
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from utils import RecordingMail

from cocat.app.accounts import reset_link, start_verification, verification_link
from cocat.app.db import User
from cocat.app.mail import PLACEHOLDER_DOMAIN
from cocat.app.rooms import list_user_rooms
from cocat.app.schemas import UserCreate
from cocat.app.urls import absolute_url
from cocat.app.users import UserManager, make_user_manager

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    """aiosqlite only runs on asyncio, so these tests cannot use trio."""
    return "asyncio"


def make_request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "https",
            "server": ("testserver", 443),
            "path": "/ui/verify",
            "root_path": "",
            "query_string": b"",
            "headers": [],
        }
    )


def manager_for(
    session: AsyncSession, mail: RecordingMail | None = None
) -> UserManager:
    return make_user_manager(
        SQLAlchemyUserDatabase(session, User), "secret", cast(Any, mail)
    )


async def unverified(session: AsyncSession, email: str = "new@example.org") -> User:
    user = User(
        email=email,
        hashed_password="x",
        is_active=True,
        is_superuser=False,
        is_verified=False,
    )
    session.add(user)
    await session.commit()
    return user


def test_the_verification_link_keeps_the_token_in_the_fragment() -> None:
    link = verification_link(make_request(), "tok")

    assert link == "https://testserver/ui/verify#tok"


def test_the_reset_link_keeps_the_token_in_the_fragment() -> None:
    link = reset_link(make_request(), "tok")

    assert link == "https://testserver/ui/reset-password#tok"


async def test_verification_is_skipped_without_a_mailer(session: AsyncSession) -> None:
    user = await unverified(session)

    await start_verification(manager_for(session), user, make_request())

    assert user.is_verified


async def test_verification_is_requested_with_a_mailer(session: AsyncSession) -> None:
    user = await unverified(session)
    mail = RecordingMail()

    await start_verification(manager_for(session, mail), user, make_request())

    assert not user.is_verified
    [(to, _subject, body)] = mail.mails_to(user.email)
    assert to == user.email
    assert "/ui/verify#tok" not in body
    assert "/ui/verify#" in body


async def test_a_placeholder_address_is_left_unverified(
    session: AsyncSession,
) -> None:
    user = await unverified(session, f"0000-0002@orcid@{PLACEHOLDER_DOMAIN}")
    mail = RecordingMail()

    await start_verification(manager_for(session, mail), user, make_request())

    assert not user.is_verified
    assert mail.sent == []


async def test_verify_account_verifies_an_unverified_user(
    session: AsyncSession,
) -> None:
    user = await unverified(session)

    await manager_for(session).verify_account(user, make_request())

    assert user.is_verified


async def test_verify_account_leaves_a_verified_user_alone(
    session: AsyncSession,
) -> None:
    user = await unverified(session)
    user.is_verified = True
    await session.commit()
    mail = RecordingMail()

    await manager_for(session, mail).verify_account(user, make_request())

    assert mail.sent == []


async def test_registering_creates_a_personal_room_and_verifies(
    session: AsyncSession,
) -> None:
    manager = manager_for(session)

    user = await manager.create(UserCreate(email="new@example.org", password="pwd"))

    assert user.is_verified
    rooms = await list_user_rooms(session, user.id)
    assert [room.name for room, _role in rooms] == ["Personal"]


async def test_registering_asks_for_verification_when_a_mailer_is_set(
    session: AsyncSession,
) -> None:
    mail = RecordingMail()

    user = await manager_for(session, mail).create(
        UserCreate(email="new@example.org", password="pwd"),
        request=make_request(),
    )

    assert not user.is_verified
    [(to, _subject, body)] = mail.mails_to("new@example.org")
    assert to == "new@example.org"
    assert "/ui/verify#" in body


@pytest.mark.parametrize("address", ["new@example.org"])
async def test_the_mail_body_never_names_the_token_alone(
    session: AsyncSession, address: str
) -> None:
    user = await unverified(session, address)
    mail = RecordingMail()

    await start_verification(manager_for(session, mail), user, make_request())

    [(_, _, body)] = mail.mails_to(address)
    token = body.split("/ui/verify#", 1)[1].split()[0]
    assert len(token) > 20


def test_the_public_url_falls_back_to_the_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COCAT_PUBLIC_URL", raising=False)

    assert (
        absolute_url(make_request(), "/verify#tok")
        == "https://testserver/ui/verify#tok"
    )
