import pytest
from pycrdt import (
    Doc,
    YMessageType,
    create_awareness_message,
    create_sync_message,
    create_update_message,
)
from pycrdt._sync import create_sync_step2_message
from wiredb import AsyncChannel

from cocat.app.yjs import RoomChannel

pytestmark = pytest.mark.anyio


@pytest.fixture(params=["asyncio", "trio"])
def anyio_backend(request: pytest.FixtureRequest) -> str:
    """No database is involved, so the filter runs on both anyio backends."""
    return str(request.param)


class FakeChannel(AsyncChannel):
    """Yields the given frames, then stops; records what is sent to it."""

    def __init__(self, *messages: bytes) -> None:
        self.messages = list(messages)
        self.sent: list[bytes] = []

    @property
    def id(self) -> str:
        return "room"

    async def receive(self) -> bytes:
        if not self.messages:
            raise StopAsyncIteration
        return self.messages.pop(0)

    async def send(self, message: bytes) -> None:
        self.sent.append(message)


async def test_a_read_only_channel_drops_writes_and_delivers_reads() -> None:
    step1 = create_sync_message(Doc())
    channel = RoomChannel(
        FakeChannel(
            create_update_message(b"\x00\x00"),
            create_sync_step2_message(b"\x00\x00"),
            step1,
        ),
        writes=False,
    )

    assert await channel.receive() == step1


async def test_a_writable_channel_delivers_writes() -> None:
    update = create_update_message(b"\x00\x00")
    channel = RoomChannel(FakeChannel(update))

    assert await channel.receive() == update


async def test_a_frame_too_short_to_read_is_dropped_for_everyone() -> None:
    step1 = create_sync_message(Doc())
    channel = RoomChannel(
        FakeChannel(
            b"", bytes([YMessageType.SYNC]), create_awareness_message(b"{}"), step1
        ),
    )

    assert await channel.receive() == create_awareness_message(b"{}")


async def test_the_channel_stops_when_the_wrapped_channel_stops() -> None:
    with pytest.raises(StopAsyncIteration):
        await anext(RoomChannel(FakeChannel()))


async def test_sending_reaches_the_wrapped_channel() -> None:
    inner = FakeChannel()
    channel = RoomChannel(inner)
    frame = create_sync_message(Doc())

    await channel.send(frame)

    assert inner.sent == [frame]


def test_the_id_is_the_wrapped_channel_id() -> None:
    assert RoomChannel(FakeChannel()).id == "room"
