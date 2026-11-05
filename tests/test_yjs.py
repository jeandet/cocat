import pytest
from pycrdt import (
    Doc,
    YMessageType,
    YSyncMessageType,
    create_awareness_message,
    create_sync_message,
    create_update_message,
)
from pycrdt._sync import create_sync_step2_message
from wiredb import AsyncChannel

from cocat.app.yjs import DOCUMENT_WRITES, ReadOnlyChannel, is_document_write

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


def test_a_sync_step2_message_is_a_document_write() -> None:
    # pycrdt exports the step-2 builder only from its private sync module.
    assert is_document_write(create_sync_step2_message(b"\x00\x00"))


def test_an_update_message_is_a_document_write() -> None:
    assert is_document_write(create_update_message(b"\x00\x00"))


def test_a_sync_step1_message_is_not_a_document_write() -> None:
    assert not is_document_write(create_sync_message(Doc()))


def test_an_awareness_message_is_not_a_document_write() -> None:
    assert not is_document_write(create_awareness_message(b"{}"))


def test_a_short_message_is_not_a_document_write() -> None:
    assert not is_document_write(b"")
    assert not is_document_write(bytes([YMessageType.SYNC]))


def test_only_the_two_update_sub_types_are_writes() -> None:
    assert DOCUMENT_WRITES == frozenset(
        {YSyncMessageType.SYNC_STEP2, YSyncMessageType.SYNC_UPDATE}
    )


async def test_the_channel_drops_writes_and_delivers_reads() -> None:
    step1 = create_sync_message(Doc())
    channel = ReadOnlyChannel(
        FakeChannel(
            create_update_message(b"\x00\x00"),
            create_sync_step2_message(b"\x00\x00"),
            step1,
        )
    )

    assert await channel.receive() == step1


async def test_the_channel_stops_when_the_wrapped_channel_stops() -> None:
    with pytest.raises(StopAsyncIteration):
        await anext(ReadOnlyChannel(FakeChannel()))


async def test_sending_reaches_the_wrapped_channel() -> None:
    inner = FakeChannel()
    channel = ReadOnlyChannel(inner)
    frame = create_sync_message(Doc())

    await channel.send(frame)

    assert inner.sent == [frame]


def test_the_id_is_the_wrapped_channel_id() -> None:
    assert ReadOnlyChannel(FakeChannel()).id == "room"
