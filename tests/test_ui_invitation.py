import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import pytest
from conftest import UI
from nicegui.elements.link import Link
from sqlalchemy.ext.asyncio import AsyncSession
from utils import (
    click,
    ensure_user,
    fill,
    in_session,
    rooms_of,
    should_notify,
    user_by_email,
)

from cocat.app.db import Role
from cocat.app.invitations import create_invitation
from cocat.app.rooms import create_room

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    # aiosqlite only runs on asyncio
    return "asyncio"


@dataclass
class Seeded:
    room_id: str
    room_name: str
    token: str
    owner_email: str


async def invitation_for(
    ui: UI, email: str | None = None, max_uses: int | None = None
) -> Seeded:
    """A room owned by someone else and an invitation for `ui` to join it."""
    owner_email = f"owner-{uuid4().hex}@foo.com"
    room_name = f"inviting-{uuid4().hex}"
    await ensure_user(ui.db_path, owner_email)

    async def seed(session: AsyncSession) -> Seeded:
        owner = await user_by_email(session, owner_email)
        room = await create_room(session, owner, room_name)
        _, token = await create_invitation(
            session,
            room.id,
            owner,
            Role.editor,
            email=ui.email if email is None else email,
            max_uses=max_uses,
        )
        return Seeded(room.id, room_name, token, owner_email)

    return await in_session(ui.db_path, seed)


def answer_fragment(ui: UI, token: str) -> None:
    """What the browser answers when the page asks for `location.hash`."""
    rules = ui.user.javascript_rules
    rules[re.compile(r"window\.location\.hash")] = lambda _: token


def link_target(ui: UI, marker: str) -> str:
    element = next(iter(ui.user.find(marker=marker).elements))
    assert isinstance(element, Link)
    return str(element.props["href"])


async def test_shows_what_you_are_joining(ui: UI) -> None:
    seeded = await invitation_for(ui)
    await ui.log_in()
    answer_fragment(ui, seeded.token)

    await ui.user.open("/ui/invitation")

    await ui.user.should_see(seeded.room_name)
    await ui.user.should_see("editor")
    await ui.user.should_see(seeded.owner_email)
    await ui.user.should_see(marker="accept-invitation")


async def test_accept_joins_the_room(ui: UI) -> None:
    seeded = await invitation_for(ui)
    await ui.log_in()
    answer_fragment(ui, seeded.token)
    await ui.user.open("/ui/invitation")
    await ui.user.should_see(marker="accept-invitation", retries=30)

    click(ui.user, "accept-invitation")

    await should_notify(ui.user, f"You joined {seeded.room_name}")
    await ui.user.should_see(marker="leave-room", retries=10)
    assert seeded.room_id in (await rooms_of(ui.db_path, ui.email)).values()


async def test_accept_second_time_reports_exhausted(ui: UI) -> None:
    seeded = await invitation_for(ui)
    await ui.log_in()
    answer_fragment(ui, seeded.token)
    await ui.user.open("/ui/invitation")
    await ui.user.should_see(marker="accept-invitation", retries=30)
    click(ui.user, "accept-invitation")
    await should_notify(ui.user, f"You joined {seeded.room_name}")
    await ui.user.should_see(marker="leave-room", retries=30)

    await ui.user.open("/ui/invitation")

    await ui.user.should_see("This invitation is not valid anymore")
    await ui.user.should_not_see(marker="accept-invitation")


async def test_wrong_email_is_refused(ui: UI) -> None:
    seeded = await invitation_for(ui, email=f"other-{uuid4().hex}@foo.com")
    await ui.log_in()
    answer_fragment(ui, seeded.token)
    await ui.user.open("/ui/invitation")
    await ui.user.should_see(marker="accept-invitation", retries=30)

    click(ui.user, "accept-invitation")

    await should_notify(ui.user, "This invitation is for another email address")


async def test_invalid_token_is_reported(ui: UI) -> None:
    await ui.log_in()
    answer_fragment(ui, "nope")

    await ui.user.open("/ui/invitation")

    await ui.user.should_see("This invitation is not valid anymore")
    await ui.user.should_not_see(marker="accept-invitation")


async def test_empty_fragment_falls_back_to_the_paste_box(ui: UI) -> None:
    await ui.log_in()
    answer_fragment(ui, "")

    await ui.user.open("/ui/invitation")

    await ui.user.should_see(marker="paste-link")
    await ui.user.should_not_see(marker="accept-invitation")


async def test_missing_fragment_falls_back_to_the_paste_box(ui: UI) -> None:
    await ui.log_in()
    ui.user.javascript_rules[re.compile(r"window\.location\.hash")] = lambda _: None

    await ui.user.open("/ui/invitation")

    await ui.user.should_see(marker="paste-link")


async def test_logged_out_shows_a_login_link(ui: UI) -> None:
    seeded = await invitation_for(ui)
    answer_fragment(ui, seeded.token)

    await ui.user.open("/ui/invitation")

    await ui.user.should_see(seeded.room_name)
    await ui.user.should_not_see(marker="accept-invitation")
    query = urlsplit(link_target(ui, "login-to-accept")).query
    assert parse_qs(query) == {"next": ["/ui/invitation"]}


async def test_paste_box_reads_a_link(ui: UI) -> None:
    seeded = await invitation_for(ui)
    await ui.log_in()
    answer_fragment(ui, "")
    await ui.user.open("/ui/invitation")
    await ui.user.should_see(marker="paste-link", retries=30)

    fill(
        ui.user, "paste-link", f"https://cocat.example.org/ui/invitation#{seeded.token}"
    )
    click(ui.user, "continue")

    await ui.user.should_see(seeded.room_name, retries=10)
    await ui.user.should_see(marker="accept-invitation", retries=10)


async def test_paste_box_reads_a_bare_token(ui: UI) -> None:
    seeded = await invitation_for(ui)
    await ui.log_in()
    answer_fragment(ui, "")
    await ui.user.open("/ui/invitation")
    await ui.user.should_see(marker="paste-link", retries=30)

    fill(ui.user, "paste-link", seeded.token)
    click(ui.user, "continue")

    await ui.user.should_see(seeded.room_name, retries=10)
