# Phase 5 — Viewer write-blocking on the websocket

## Challenge — what I changed in this draft and why

Checked against the installed `wiredb 0.7.1` / `pycrdt 0.12.50` and the current tree.

1. **The wrapper only needs `receive()`.** `wiredb.server.Room.serve` iterates with
   `async for message in client`, and `AsyncChannel.__anext__` is
   `return await self.receive()` (`wiredb/channel.py`). Overriding `__anext__` *and*
   `receive` is two paths that can drift; one `receive` override covers both call
   styles. The draft's "`receive` must go through the filter too" test disappears with
   the second method.
2. **`tests/test_yjs.py` must run on trio as well.** The draft copied the asyncio-only
   `anyio_backend` fixture from `tests/test_actions.py`, whose reason is aiosqlite; this
   module touches no database, so pinning it to asyncio would silently drop trio
   coverage of the new filter — the failure mode of the earlier
   `3917cc8` regression. No fixture override: `pytestmark = pytest.mark.anyio` only.
3. **`create_sync_step2_message` is not exported by `pycrdt`** (only
   `create_sync_message`, `create_update_message`, `create_awareness_message` and
   `handle_sync_message` are). The test imports it from `pycrdt._sync`, with a comment
   saying why; the alternative (hand-built bytes) would test the test.
4. **The awareness integration test is dropped.** It asserted "the state is still empty"
   after an awareness frame plus a dropped write — that is the drop test again, and the
   awareness claim is already checked directly in `tests/test_yjs.py`. Phase 6 will add
   its own awareness test when it has behaviour to assert.
5. **Owner/editor wording fixed in Task 3's tests**: the read-only assertions target the
   viewer's own user object, and "no role select" is asserted with the exact marker
   (`role-<owner id>`), since `find` matches markers by equality.
6. **Kicks are unconditional on a role change** (also when the role is unchanged):
   one branch fewer, and closing a socket that is about to be re-opened costs a
   reconnect only.

Verified and left as written: writes only reach the document through
`handle_sync_message` inside `Room.serve` (the server's own `new_transaction()` calls are
reads/broadcasts), `websocket_auth` has exactly one caller, `Role` is a `str` enum so
`is Role.viewer` is safe on ORM values, and `Connections.close(room_id, user_id)` closes
that member's sockets with 1008 and nothing else.


> **For the implementing agent:** this plan is written for a fresh session. Read
> `CLAUDE.md`, `docs/superpowers/specs/2026-11-04-self-service-rooms-design.md` §"Roles"
> and §"Websocket enforcement", and `.../scratchpad/overnight-rules.md` before starting.
> Implement one task at a time, test first, commit after the full gate passes.

## Goal

Make the `viewer` role trustworthy end to end:

1. A viewer's websocket never mutates the room document. The server drops Yjs
   document-update messages (`sync step 2` and `update`) from that socket, while still
   answering `sync step 1` and still broadcasting every update *to* it.
2. Changing a member's role closes their open sockets, so a stale role snapshot cannot
   outlive the change (this is the design's "role change" clause, not a nicety).
3. The API and the UI can then hand out and assign `viewer`: `PATCH` member role,
   invitation role, role select in the members table, role select for invitations.
4. Documentation and version (`0.15.0`) describe the enforced role.

Non-goals: plugin-side editing lock (phase 4, external repo), awareness/presence
fan-out (phase 6 — the server still ignores awareness frames; this plan only guarantees
they are *not* dropped by the filter), per-frame re-authorisation (a role change kicks
instead, see Decisions).

## Architecture

`wiredb.Room.serve()` owns the read loop: it iterates the channel cocat hands it
(`async for message in client`), and applies every `YMessageType.SYNC` frame through
`pycrdt.handle_sync_message`. It has no role concept and no hook, so the filter must sit
in front of it.

The smallest correct interception point is the channel object cocat already builds per
connection (`YWebSocket`). Wrap it in a decorator channel that yields only frames the
viewer is allowed to trigger, and hand *that* to `room.serve()`. `wiredb` is untouched,
and the free-text `Room.serve(client: AsyncChannel)` signature makes the wrapper a
first-class citizen rather than a hack.

```
connect_room (app.py)
  ├─ backend.websocket_auth → (websocket, user, role)   # role already queried today
  ├─ YWebSocket(websocket, room_id)                      # transport, unchanged
  ├─ ReadOnlyChannel(ywebsocket) if role is viewer       # new, app/yjs.py
  └─ room.serve(channel)                                 # wiredb, unchanged
```

Broadcast stays server → client and is untouched: `Room.run` sends to every client in
`_clients`, so viewers keep receiving updates with no extra code.

Role changes: the websocket role is read once, at accept. `Connections.close(room_id,
user_id)` already exists and sends `WS_1008_POLICY_VIOLATION`; today it is called from
`RoomActions.delete_room` and `RoomActions.remove_member`. Add the same-shaped
`RoomActions.set_member_role` so the service mutation and the kick stay one call at one
abstraction level (the actions docstring already says effects that revoke access live
there). The client reconnects and a fresh role snapshot is taken by
`websocket_auth`.

## Tech Stack

Python 3.14 (`requires-python >=3.10`), FastAPI 0.162 / Starlette 1.10, wiredb 0.7.1,
pycrdt 0.12.50, NiceGUI 3.19, SQLAlchemy async, pytest + anyio (asyncio backend),
coverage with `--fail-under=100`, ruff, mypy.

## Spec

- Design doc lines 61-66 (role table: viewer reads, never writes) and 110-115
  ("the server drops document-update messages coming from a viewer's socket … It still
  answers their sync requests … Removal, role change, room deletion: the server closes
  that user's open sockets for the room").
- Design doc 188-189: until phase 5 the UI offers only owner/editor; after it, viewer is
  a normal choice.

## Decisions (and why)

1. **Wrap, don't fork.** `wiredb.Room.serve` accepts any `AsyncChannel`
   (`wiredb/channel.py:51-99`); the ABC only requires `id`, `send`, `receive` and
   supplies `__aiter__`/`__anext__` defaults, so a delegating wrapper is supported
   behaviour. Forking `Room` would duplicate the sync handling and drift on upgrade.
2. **Filter in the channel, not in a patched `handle_sync_message`.** By the time
   wiredb sees the frame it is about to be applied; a wrapper can drop it before that
   with three lines and zero knowledge of pycrdt internals beyond the two type bytes.
3. **Drop, don't close.** The spec says "drops". Closing would turn a plugin bug (a
   viewer's UI still emitting edits) into a disconnect loop; dropping keeps the socket
   read-only and self-healing.
4. **Pass through everything that is not a document write.** Awareness frames
   (`YMessageType.AWARENESS`) are ignored by wiredb 0.7.1 and will be consumed in phase
   6; dropping them here would silently break that phase. `sync step 1` is a read
   (`ydoc.get_update(state)`), so it is allowed. Unknown frame types are passed through
   untouched — wiredb ignores them, and an explicit allow-list would couple cocat to
   every future protocol addition.
5. **A kick is unconditional on a role change**, including when the value is unchanged:
   one branch fewer, and the cost is a reconnect that was about to happen anyway.
6. **Role snapshot at accept + kick on change** is the simplest correct model: no
   per-frame DB reads, no shared mutable role state, one code path for "access changed
   while connected" that already exists. A kick also matches the UI, which reconnects
   and re-renders.
7. **No kick on `transfer_ownership`** (owner and editor both write; the new owner needs
   no new permission on the websocket) and **no kick on `accept_invitation`** (the
   invitation path only ever *raises* a role through `_add_member`, so an open socket's
   snapshot is stale-low, never stale-high). Both are recorded here so a reviewer does
   not read the omission as an oversight.
8. **`websocket_auth` returns the role it already fetched.** It calls `get_role` for the
   membership check; returning it avoids a second query and keeps the route free of DB
   access.

## Global constraints

- Full gate before each commit (from `.../scratchpad/overnight-rules.md`):
  ```sh
  export PATH=$PWD/.venv/bin:$PATH; coverage run -m pytest tests -q && coverage report --fail-under=100 && ruff check src tests && ruff format --check src tests && mypy src tests
  ```
- New code must be covered; no `# pragma: nocover` on `app/yjs.py` or on the new route
  branch. `YWebSocket` keeps its existing pragma (it is transport glue exercised only by
  the subprocess test).
- No narrating comments. `yjs.py` gets one short docstring for `is_document_write`
  pointing at the Yjs message layout (`pycrdt/_sync.py`), nothing else.
- Tests: one run at a time, foreground. Never weaken an assertion to make it pass.
- Commit per task, explicit paths, trailer
  `Co-Authored-By: opencode <noreply@opencode.ai>`, never push.

## Review focus

- Is `sync step 1` really read-only, and is `sync step 2` really a write? (Verified
  below: `handle_sync_message` applies the payload only for step 2 / update.)
- Does a viewer still receive the initial `create_sync_message` and every later update?
- Does dropping happen *before* `doc.new_transaction()`, so a dropped frame never
  touches the document?
- Is the role comparison `is Role.viewer` (enum identity, `Role(str, Enum)`) and does
  `websocket_auth` still close non-members with 1008?
- Does a role change kick *all* of a user's sockets for that room and nothing else?
- Does the UI stay read-only for the viewer itself (no role selects, no remove buttons,
  no invite row) while the owner sees the new selects?
- Coverage: the `ReadOnlyChannel` loop must have a test where a *run of dropped frames*
  precedes a delivered one, not just a single drop.

## File map

| File | Change |
|---|---|
| `src/cocat/app/yjs.py` | **new** — `is_document_write`, `ReadOnlyChannel` |
| `src/cocat/app/users.py` | `websocket_auth` returns `(websocket, user, role)` |
| `src/cocat/app/app.py` | route unpacks the role and wraps viewer channels |
| `src/cocat/app/actions.py` | `RoomActions.set_member_role` (mutate + kick) |
| `src/cocat/app/routes.py` | `patch_member` goes through `actions.set_member_role` |
| `src/cocat/app/schemas.py` | `RoleUpdate.role` and `InvitationCreate.role` accept `viewer` |
| `src/cocat/ui/room.py` | role select per member, invitation role select, heading wording |
| `tests/test_yjs.py` | **new** — unit tests for the predicate and the channel |
| `tests/test_routes.py` | websocket tests, role-assignment tests, replace `test_viewer_role_not_assignable_yet` |
| `tests/test_actions.py` | `set_member_role` closes the target's sockets |
| `tests/test_ui_room.py` | role selects, viewer read-only page, kick on role change |
| `tests/utils.py` | `pick(user, marker, value)` helper for `ui.select` |
| `docs/usage.md` | enforcement paragraph, role column/row wording |
| `CHANGELOG.md`, `pyproject.toml` | `0.15.0` |

---

## Task 1 — Drop document writes from viewer channels

**Files:** create `src/cocat/app/yjs.py`, `src/cocat/app/app.py`,
`src/cocat/app/users.py`; create `tests/test_yjs.py`, extend `tests/test_routes.py`.

**Interfaces**

```python
# src/cocat/app/yjs.py
from wiredb import AsyncChannel
from pycrdt import YMessageType, YSyncMessageType

DOCUMENT_WRITES: tuple[int, ...] = (
    YSyncMessageType.SYNC_STEP2,
    YSyncMessageType.SYNC_UPDATE,
)


def is_document_write(message: bytes) -> bool:
    """True for the sync frames that mutate the shared document.

    A Yjs sync message is `[YMessageType.SYNC, sync sub-type, payload]`
    (`pycrdt/_sync.py`); only step 2 and update carry an applied update.
    """


class ReadOnlyChannel(AsyncChannel):
    """Channel decorator that hides document writes from the room."""

    def __init__(self, channel: AsyncChannel) -> None: ...
    @property
    def id(self) -> str: ...
    async def receive(self) -> bytes: ...
    async def send(self, message: bytes) -> None: ...
```

`receive` body: loop over `self._channel.receive()`, returning the first frame that is
not a document write; the inner channel's `StopAsyncIteration` propagates out of the
loop and ends the server's `async for`. Do not delegate to `self._channel.receive()`
unfiltered. `id` and `send` delegate. Only `id`, `send` and `receive` are abstract
(`wiredb/channel.py`), and the inherited `__anext__` calls this `receive`, so the
filter cannot be bypassed by a caller that uses `async for`.

```python
# src/cocat/app/users.py – websocket_auth
async def websocket_auth(...) -> tuple[WebSocket, User, Role] | None:
    async with db.async_session_maker() as session:
        user = await user_from_token(...)
        if user is not None:
            role = await get_role(session, room_id, user.id)
            if role is not None:
                return websocket, user, role

    await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
    return None
```
Import `Role` from `.db`. The refusal path (single 1008 close, `None`) is unchanged for
both "no session" and "not a member"; only the success value grows the role.

```python
# src/cocat/app/app.py – connect_room
websocket, user, role = authenticated
...
channel = ReadOnlyChannel(ywebsocket) if role is Role.viewer else ywebsocket
await room.serve(channel)
```
Import `Role` from `.db` and `ReadOnlyChannel` from `.yjs`. `# pragma: nocover` stays on
`CocatApp`/`YWebSocket`; `ReadOnlyChannel` and `is_document_write` must be fully covered.

**Steps**

1. Write `tests/test_yjs.py` (async, `pytestmark = pytest.mark.anyio` and **no**
   `anyio_backend` fixture: no database is involved, so these run on asyncio and trio,
   like `tests/test_file.py`) with:

   ```python
   class FakeChannel(AsyncChannel):
       """Yields the given frames, then stops; records what is sent to it."""
       def __init__(self, *messages: bytes) -> None:
           self.messages = list(messages)
           self.sent: list[bytes] = []
       @property
       def id(self) -> str: return "room"
       async def receive(self) -> bytes:
           if not self.messages:
               raise StopAsyncIteration
           return self.messages.pop(0)
       async def send(self, message: bytes) -> None: self.sent.append(message)
   ```

   - `test_a_sync_step2_message_is_a_document_write` —
     `assert is_document_write(create_sync_step2_message(b"\x00\x00"))` (imported from
     `pycrdt._sync`: `pycrdt` exports only `create_sync_message`,
     `create_update_message`, `create_awareness_message` and `handle_sync_message`).
   - `test_an_update_message_is_a_document_write` —
     `assert is_document_write(create_update_message(b"\x00\x00"))`.
   - `test_a_sync_step1_message_is_not_a_document_write` —
     `assert not is_document_write(create_sync_message(Doc()))`.
   - `test_an_awareness_message_is_not_a_document_write` —
     `assert not is_document_write(create_awareness_message(b"{}"))`.
   - `test_a_short_message_is_not_a_document_write` — `assert not
     is_document_write(b"")` and `assert not is_document_write(bytes([YMessageType.SYNC]))`.
   - `test_the_channel_drops_writes_and_delivers_reads` — `channel =
     ReadOnlyChannel(FakeChannel(update, step2, step1))`; `assert await anext(channel) ==
     step1` (proves a *run* of drops is skipped, not one).
   - `test_the_channel_stops_when_the_wrapped_channel_stops` — `with
     pytest.raises(StopAsyncIteration): await anext(ReadOnlyChannel(FakeChannel()))`.
   - `test_sending_reaches_the_wrapped_channel` — `await channel.send(frame)` then
     `assert inner.sent == [frame]`.
   - `test_the_id_is_the_wrapped_channel_id` — `assert channel.id == "room"`.

   FakeChannel must implement `receive` as well (the ABC requires it): `async def
   receive(self) -> bytes: return await self.__anext__()`.

2. Run `pytest tests/test_yjs.py -q` → it must fail with `ImportError`/collection
   error (the module does not exist yet), which is the correct first failure.
3. Write `src/cocat/app/yjs.py` exactly as specified in **Interfaces**, then rerun
   `pytest tests/test_yjs.py -q` → green. Wire `websocket_auth` and `connect_room`
   (both are `# pragma: nocover` today, so they earn their coverage from the
   subprocess/websocket tests, not from the unit run).
4. Add the websocket tests in `tests/test_routes.py` (sync). Helpers (module level, next
   to `create_room`/`invite`):

   ```python
   def document_update() -> bytes:
       """An update that adds {"key": "value"} to the map named "content"."""
       doc = Doc()
       doc.get("content", type=Map)["key"] = "value"
       return create_update_message(doc.get_update())

   def content_of(frame: bytes) -> dict[str, str]:
       assert frame[0] == YMessageType.SYNC
       assert frame[1] == YSyncMessageType.SYNC_STEP2
       doc = Doc()
       handle_sync_message(frame[1:], doc)
       return dict(doc.get("content", type=Map))

   def content_after_sync_request(websocket) -> dict[str, str]:
       """Ask for everything the room has, from the empty state."""
       websocket.send_bytes(create_sync_message(Doc()))
       return content_of(websocket.receive_bytes())

   def add_member_with(app_client: AppClient, room_id: str, email: str, role: Role) -> None:
       async def work(session: AsyncSession) -> None:
           user = await user_by_email(session, email)
           await add_member(session, room_id, user.id, role)
       anyio.run(in_session, app_client.db_path, work)
   ```
   (`make_user`, `login`, `ws_headers` come from `conftest`; `user_by_email`,
   `in_session` from `utils`; `add_member`, `Role` from `cocat.app.rooms`/`cocat.app.db`.
   `login(app_client.client, ...)` and `make_user(app_client.db_path, ...)` are the
   existing call shapes; one TestClient holds one cookie, so re-login before each socket
   and capture `headers = ws_headers(app_client.client)` at that moment.)

   - `test_a_viewer_cannot_write_to_the_room`
     ```python
     make_user(app_client.db_path, "owner@test.org")
     login(app_client.client, "owner@test.org")
     room = create_room(app_client)
     make_user(app_client.db_path, "viewer@test.org")
     add_member_with(app_client, room["id"], "viewer@test.org", Role.viewer)
     login(app_client.client, "viewer@test.org")
     headers = ws_headers(app_client.client)
     url = f"/room/{room['id']}"
     with app_client.client.websocket_connect(url, headers=headers) as websocket:
         assert websocket.receive_bytes()               # the server's sync message
         websocket.send_bytes(document_update())
         assert content_after_sync_request(websocket) == {}
     ```
     The empty map is the whole point: had the update been applied, the step-2 reply
     built from the room document would contain `{"key": "value"}`.
   - `test_a_viewer_still_answers_sync_requests` — owner as above, owner socket sends
     `document_update()` and consumes its own broadcast (`assert
     websocket.receive_bytes()`), socket closed; then viewer logs in, connects, consumes
     the initial frame, and `content_after_sync_request(websocket) == {"key": "value"}`.
     This proves the viewer's step 1 is answered *from the room state*, not with `None`.
   - `test_a_viewer_receives_an_update_from_the_owner` — owner and viewer sockets open at
     the same time (connect the viewer first, consume its initial frame, then log in as
     the owner and open the second socket, consuming its initial frame); owner sends
     `document_update()`; the viewer's next frame must be an update:
     ```python
     frame = viewer.receive_bytes()
     assert frame[0] == YMessageType.SYNC
     assert frame[1] == YSyncMessageType.SYNC_UPDATE
     doc = Doc()
     handle_sync_message(frame[1:], doc)
     assert dict(doc.get("content", type=Map)) == {"key": "value"}
     ```
     Decoding into a *fresh* `Doc` proves the frame carried the update rather than the
     viewer already holding it.
   - `test_an_editor_can_write_to_the_room` — same as the first test with
     `Role.editor`; `content_after_sync_request(websocket) == {"key": "value"}`.

5. Run `pytest tests/test_routes.py -q -k "viewer or editor or websocket"`; then the
   full gate; commit
   `Drop document writes from viewer websockets` with a body explaining that the filter
   is a channel decorator so `wiredb` stays the single sync implementation.

---

## Task 2 — A role change closes the member's open sockets

**Files:** `src/cocat/app/actions.py`, `src/cocat/app/routes.py`;
`tests/test_actions.py`, `tests/test_routes.py`.

**Interfaces**

```python
# src/cocat/app/actions.py
async def set_member_role(
    self, session: AsyncSession, room_id: str, actor: User, member_id: UUID, role: Role
) -> None:
    """Set a member's role and close their sockets.

    The websocket role is fixed at accept time, so a downgrade only takes effect
    after the socket reconnects.
    """
    await set_member_role(session, room_id, actor, member_id, role)
    await self.connections.close(room_id, member_id)
```
(Name clash with the service is intentional and mirrors `remove_member`, which already
does `remove_member(...)` inside `RoomActions.remove_member`; import the service
function under the same name.)

`routes.py::patch_member` becomes:

```python
await actions.set_member_role(session, room_id, user, user_id, Role(body.role))
```

**Steps**

1. `tests/test_actions.py`: `test_set_member_role_closes_that_socket`
   ```python
   owner = await make_orm_user(session, "owner@example.com")
   member = await make_orm_user(session, "member@example.com")
   room = await create_room(session, owner, "Room")
   await add_member(session, room.id, member.id, Role.editor)
   connections = Connections()
   member_socket = connect(connections, room.id, member.id)
   owner_socket = connect(connections, room.id, owner.id)
   actions = RoomActions(connections, update_dir)

   await actions.set_member_role(session, room.id, owner, member.id, Role.viewer)

   assert member_socket.closed == [1008]
   assert owner_socket.closed == []
   assert await get_role(session, room.id, member.id) is Role.viewer
   ```
2. `tests/test_routes.py`:
   - `test_downgrading_a_member_closes_their_socket`
     ```python
     owner logs in, creates room, invites+joins editor ("editor@test.org")
     editor logs in, opens /room/{id}, receives the step1 frame
     owner logs back in (re-login to restore owner cookies) and PATCHes
       {"role": "viewer"} → 204
     with pytest.raises(WebSocketDisconnect) as excinfo: editor_ws.receive_bytes()
     assert excinfo.value.code == 1008
     editor reconnects and `content_after_sync_request(ws) == {}` after sending
     `document_update()` → the reconnect is read-only
     ```
   - `test_changing_a_role_does_not_close_other_members` — a third member's socket
     still receives the owner's update after the downgrade.
3. `pytest tests/test_actions.py tests/test_routes.py -q`; full gate; commit
   `Close sockets when a member's role changes`.

---

## Task 3 — API and UI offer the viewer role

**Files:** `src/cocat/app/schemas.py`, `src/cocat/ui/room.py`, `tests/utils.py`,
`tests/test_routes.py`, `tests/test_ui_room.py`.

**Interfaces**

```python
# schemas.py
class RoleUpdate(BaseModel):
    role: Literal["viewer", "editor"]

class InvitationCreate(BaseModel):
    ...
    role: Literal["viewer", "editor"] = "editor"
```

```python
# tests/utils.py
def pick(user: User, marker: str, value: str) -> None:
    """Choose a value on the select carrying this marker, without waiting.

    The change handler is scheduled as a background task, so callers wait for its
    notification (`should_notify`) before asserting on the result.
    """
    element = next(iter(user.find(marker=marker).elements))
    cast(Select, element).value = value
```
(`ValueElement.value = x` calls `_handle_value_change` →
`nicegui/elements/mixins/value_element.py:128-142`; `events.handle_event` schedules an
awaitable result as a background task — `nicegui/events.py:473-505`. Import `Select` from
`nicegui.elements.select`, and `cast` from `typing`, as `value_of` already does in
`tests/test_ui_room.py`.)

UI changes in `src/cocat/ui/room.py`:

- `heading()`: `ui.label(f"Your role: {current.value}")` (fixes "You are an viewer").
- `members()`: replace the `ui.label(member_role.value)` cell for owner-actors and
  non-owner targets with a select; everyone else keeps the label:

  ```python
  async def change_role(member_id: UUID, email: str, role: str) -> None:
      changed = await act(
          ctx,
          lambda session, actor: ctx.actions.set_member_role(
              session, room_id, actor, member_id, Role(role)
          ),
      )
      if changed:
          ui.notify(f"{email} is now a {role}")
          await refresh_all()

  if current is Role.owner and member_role is not Role.owner:
      ui.select(
          ["editor", "viewer"],
          value=member_role.value,
          on_change=lambda event, id=member.id, email=member.email: change_role(
              id, email, event.value
          ),
      ).mark(f"role-{member.id}")
  else:
      ui.label(member_role.value)
  ```
  `on_change` returns a coroutine and NiceGUI schedules awaitable handler results as a
  background task (`nicegui/events.py:473-505`, "If the handler returns an awaitable, it
  is scheduled as a background task"), so the async shape is deliberate and a test must
  wait for the notification rather than assume the write happened. Keep the DB work
  inside `act()` so a stale page (role revoked meanwhile) notifies instead of raising,
  and refresh after the write so the row and the heading agree with the database.
- `invitations()`: add `invite_role` (page-scope variable, `"editor"`), a
  `ui.select(["editor", "viewer"], value=invite_role, on_change=...)` marked
  `invite-role` next to the email input, and use `Role(invite_role)` in `send_invite`
  and in `create_link`.
  Because the handler must update the outer variable, follow the existing `nonlocal
  link` pattern (`nonlocal invite_role`).

**Steps**

1. `tests/test_routes.py`: replace `test_viewer_role_not_assignable_yet` with
   `test_the_owner_can_assign_the_viewer_role` — PATCH `{"role": "viewer"}` → 204,
   `list_members(app_client, room_id)` returns that user with `"role": "viewer"`; and
   add `test_the_owner_role_is_still_not_assignable` — PATCH `{"role": "owner"}` → 422.
2. `test_an_invitation_can_grant_the_viewer_role`
   ```python
   make_user(app_client.db_path, "owner@test.org")
   login(app_client.client, "owner@test.org")
   room = create_room(app_client)
   response = app_client.client.post(
       f"/api/rooms/{room['id']}/invitations", json={"role": "viewer"}
   )
   assert response.status_code == 201
   token = response.json()["token"]
   preview = app_client.client.post("/api/invitations/preview", json={"token": token})
   assert preview.json()["role"] == "viewer"
   make_user(app_client.db_path, "guest@test.org")
   join_room(app_client, "guest@test.org", token)
   assert app_client.client.get("/api/rooms").json()[0]["role"] == "viewer"
   ```
   (`join_room` logs in as the guest, so the last call is the guest's room list;
   `InvitationPreviewOut.role` is a `Role`, serialised as its value.)
3. `tests/test_ui_room.py`:
   - `test_the_owner_changes_a_member_role` — owner and member (editor) share a room;
     owner opens it; `pick(owner, f"role-{member_id}", "viewer")`;
     `await should_notify(owner, "member@test.org is now a viewer")` (`should_notify`
     matches the whole notification string); then via `in_session` + `get_role` assert
     `Role.viewer`.
   - `test_changing_a_role_closes_the_members_socket` — same setup but
     `socket = connect(ui.cocat.connections, room_id, member_id)` before the pick;
     after the notification `assert socket.closed == [1008]`.
   - `test_the_owner_invites_a_viewer` — owner opens the room, `pick(owner,
     "invite-role", "viewer")`, `fill(owner, "invite-email", "guest@test.org")`,
     `click(owner, "invite-send")`, `await should_notify(owner, "Invitation
     created")` (the `ui` fixture has no mailer, so the token comes back as a link),
     then via `in_session` + `list_invitations(session, room_id, owner)` assert the
     newest invitation's `role is Role.viewer`.
   - `test_a_share_link_grants_the_chosen_role` — `pick(owner, "invite-role",
     "viewer")`, `click(owner, "create-invitation")`, `await
     should_notify(owner, "Share link created")`, assert the `share-link` input is
     non-empty and the newest invitation's `role is Role.viewer`.
   - `test_a_viewer_sees_a_read_only_room` — the viewer opens the shared room:
     `await viewer.user.should_see("Your role: viewer")` and `should_not_see` the
     markers `invite-send`, `create-invitation` and `role-<owner id>`.
   - `test_an_editor_sees_no_role_picker` — editor opens the room: no `role-{owner_id}`
     select, and the member rows still show plain role labels.
4. `pytest tests/test_ui_room.py tests/test_routes.py -q`; full gate; commit
   `Offer the viewer role in the API and the room page`.

---

## Task 4 — Docs and release 0.15.0

**Files:** `docs/usage.md`, `CHANGELOG.md`, `pyproject.toml`.

**Steps**

1. `docs/usage.md`:
   - Rooms section (currently "viewers are not enforced yet, so the API only hands out
     `editor`"): state that a viewer receives every change but none of their sync frames
     reach the document, that changing a member's role closes their open sockets, and
     that a client must still disable editing locally (link to the phase-4 plugin note).
   - API table row for `PATCH …/members/{user_id}`: "change a member role (owner): `viewer`
     or `editor`".
   - Invitation paragraphs: the role is chosen at creation and may be `viewer`.
   - `/ui/rooms/{room_id}` page row: the owner picks a member's role and the role a new
     invitation grants.
2. `CHANGELOG.md`: `## 0.15.0` — viewer role enforced on the websocket, role changes
   close sockets, viewer assignable/invitable, plus the two or three genuinely
   user-visible lines from the pending review fixes if they are in the same release.
3. `pyproject.toml`: `version = "0.15.0"`.
4. Full gate; commit `Release 0.15.0 with viewer enforcement`.

---

## Appendix — verified dependency facts (wiredb 0.7.1, pycrdt 0.12.50)

Paths are relative to `.venv/lib/python3.14/site-packages`.

**Message framing** (`pycrdt/_sync.py`)
- `YMessageType`: `SYNC = 0`, `AWARENESS = 1` (9-19).
- `YSyncMessageType`: `SYNC_STEP1 = 0`, `SYNC_STEP2 = 1`, `SYNC_UPDATE = 2` (22-36).
- `create_message` → `bytes([YMessageType.SYNC, sub_type]) + write_message(data)`
  (70-81); `create_sync_step1_message` (84-95), `create_sync_step2_message` (98-109),
  `create_update_message` (112-123); `create_awareness_message` →
  `bytes([AWARENESS]) + write_message(data)` (57-67); `write_message` writes a var-uint
  length then the bytes (258-268).
- `handle_sync_message(message, ydoc)` (271-301): `message[0]` is the sub-type; step 1
  reads state and returns `create_sync_step2_message(ydoc.get_update(state))` (286-290);
  step 2 / update decode the payload and apply it unless `b"\x00\x00"` (292-299).
- `create_sync_message(ydoc)` (304-317).

**Room loop** (`wiredb/server.py`)
- `Room.serve`: adds the client to `_clients` (112), sends `create_sync_message(doc)`
  (115-117), then `async for message in client` (120), and for `YMessageType.SYNC`
  frames calls `handle_sync_message(message[1:], doc)` inside `doc.new_transaction()`
  (122-126). The sub-type is never inspected → filtering must happen upstream.
- Broadcast: `Room.run` turns each doc event into `create_update_message(event.update)`
  and sends it to every client (88-92); failed sends drop the client (93-97).
- No role/read-only parameter exists on `serve` (99-132).

**Channel protocol** (`wiredb/channel.py`)
- `AsyncChannel` ABC (51-99): abstract `id` (property, 76-80), abstract `send` (86-93)
  and abstract `receive` (95-99); `__aiter__` returns self (77-78); `__anext__` defaults
  to `await self.receive()` (80-81) → a wrapper must implement `id`, `send`, `receive`
  and is free to override `__anext__`.

**Awareness**: no `AWARENESS` handling anywhere in wiredb (`grep -rn AWARENESS
site-packages/wiredb` → nothing), so those frames are currently ignored server-side and
must not be dropped by the new filter.

**Client behaviour** (`wiredb/client.py`): `AsyncClient._run` sends
`create_sync_message(doc)` on start (227-229), replies to the server's step 1 with a step
2 (233-236), and `_send_updates` pushes each local change as `create_update_message`
(253-254). So a viewer client would *try* to write — dropping step 2 and update covers
both directions of that attempt (initial catch-up and later edits).
