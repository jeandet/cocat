"""Keeping a read-only member read-only on the shared document.

`wiredb.Room.serve` applies every sync frame it reads from the channel it is
handed and has no role concept, so the filter lives in the channel.
"""

from pycrdt import YMessageType, YSyncMessageType
from wiredb import AsyncChannel

DOCUMENT_WRITES: frozenset[int] = frozenset(
    {YSyncMessageType.SYNC_STEP2, YSyncMessageType.SYNC_UPDATE}
)


def is_document_write(message: bytes) -> bool:
    """True for the frames that mutate the shared document.

    A Yjs sync frame is `[YMessageType.SYNC, sub-type, payload]` (`pycrdt/_sync.py`);
    only step 2 and update carry an update that gets applied.
    """
    return (
        len(message) >= 2
        and message[0] == YMessageType.SYNC
        and message[1] in DOCUMENT_WRITES
    )


class ReadOnlyChannel(AsyncChannel):
    """Channel decorator that hides document writes from the room."""

    def __init__(self, channel: AsyncChannel) -> None:
        self._channel = channel

    @property
    def id(self) -> str:
        return self._channel.id

    async def send(self, message: bytes) -> None:
        await self._channel.send(message)

    async def receive(self) -> bytes:
        # Iterating instead of calling `receive()` lets the wrapped channel say
        # when it is finished: a websocket turns its disconnect into the end of
        # the iteration, which the room handles as a normal close.
        async for message in self._channel:
            if not is_document_write(message):
                return message
        raise StopAsyncIteration
