"""Keeping a room read-only on the shared document.

`wiredb.Room.serve` applies every sync frame it reads from the channel it is
handed and has no role concept, so the filter lives in the channel.
"""

from pycrdt import YMessageType, YSyncMessageType
from wiredb import AsyncChannel

_DOCUMENT_WRITES: frozenset[int] = frozenset(
    {YSyncMessageType.SYNC_STEP2, YSyncMessageType.SYNC_UPDATE}
)


def _is_document_write(message: bytes) -> bool:
    """True for the frames that mutate the shared document.

    A Yjs sync frame is `[YMessageType.SYNC, sub-type, payload]` (`pycrdt/_sync.py`);
    only step 2 and update carry an update that gets applied.
    """
    return (
        len(message) >= 2
        and message[0] == YMessageType.SYNC
        and message[1] in _DOCUMENT_WRITES
    )


class RoomChannel(AsyncChannel):
    """Channel decorator that hands the room only the frames it can act on.

    Wiredb applies every sync frame it reads, so a read-only member's document
    writes are dropped here. A frame too short to be a sync message is dropped
    for everyone: the room indexes into it and would die on its own task.
    """

    def __init__(self, channel: AsyncChannel, *, writes: bool = True) -> None:
        self._channel = channel
        self._writes = writes

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
            if len(message) < 2:
                continue
            if self._writes or not _is_document_write(message):
                return message
        raise StopAsyncIteration
