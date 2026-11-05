## Quickstart

### User API

Cocat consists of the following objects:

- [DB][cocat.DB]: the object representing the database, which allows creating events and catalogues, modifying them and being notified of the changes.
- [Event][cocat.Event]: an object representing an event, which allows modifying its properties and being notified of the changes.
- [Catalogue][cocat.Catalogue]: an object representing a catalogue, which allows modifying its properties and being notified of the changes. A catalogue contains a set of events, but events are in general independent of catalogues.

User A on one machine could start creating catalogues and events:

```py
from cocat import DB

db0 = DB()
event0 = db0.create_event(
    start="2025-01-31",
    stop="2026-01-31",
    author="John",
    attributes={"foo": "bar"},
)
catalogue0 = db0.create_catalogue(
    name="cat0",
    author="Paul",
    attributes={"baz": 3}
)
catalogue0.add_events(event0)
event0.stop = "2026-01-30"
```

Up to now their data is local, but they could share the database using a web server. Here is how such a web server can be set up:

```py
from anyio import run, sleep_forever
from wire_websocket import AsyncWebSocketServer

async def main():
    async with AsyncWebSocketServer(host="localhost", port=8000):
        await sleep_forever()

run(main)
```

And here is how user A can connect to this server:

```py
from anyio import run, sleep_forever
from wire_websocket import AsyncWebSocketClient

async def main():
    async with AsyncWebSocketClient(doc=db0.doc, host="http://localhost", port=8000):
        await sleep_forever()

run(main)
```

User B on another machine could connect their database to user A's and synchronize their catalogues and events:

```py
from anyio import run, sleep_forever
from cocat import DB
from wire_websocket import AsyncWebSocketClient

async def main():
    db1 = DB()
    async with AsyncWebSocketClient(doc=db1.doc, host="http://localhost", port=8000):
        print(db1.catalogues)
        print(db1.events)

run(main)
```

### High-level API

A higher-level API is also provided, which is more suited to interactive workflows with
a Python REPL or a Jupyter notebook. This API is not async, and as such it requires manual
synchronization using `refresh()` and `save()` to pull the remote changes and push the local changes,
respectively.

```py
from cocat import (
    create_catalogue,
    create_event,
    log_in,
    log_out,
    refresh,
    save,
    set_config,
)

set_config(host="http://localhost", port=8000, file_path="updates.y")
log_in("paul@example.com", "my_password")
refresh()

catalogue0 = create_catalogue(name="cat0", author="Paul")
save()

catalogue1 = get_catalogue("56c935a2-0109-49c0-b91d-dd5b2de8feef")

event0 = create_event(
    start="2025-01-31",
    stop="2026-01-31",
    author="John",
)
save()

refresh()
event1 = get_event("497393db-7dd6-4d7b-9ff1-8a8155bfed54")

log_out()
```

`log_in` connects to the room given by `set_config(room_id=...)`; without one, it
connects to your personal room.

### CLI

A command-line interface allows to launch a server and manage users.
Because the server can only be accessed by authenticated users, one has to create users first:

```bash
cocat create-user --email "paul@example.com" --password "my_password" --db-path "users.db"
```

Every user gets a personal room named "Personal", which they own. Administrators can
create rooms and manage their members:

```bash
cocat create-room --owner "paul@example.com" --room-id "lab" --name "Lab" --db-path "users.db"
cocat add-user-to-room --email "mary@example.com" --room-id "lab" --db-path "users.db"
cocat remove-user-from-room --email "mary@example.com" --room-id "lab" --db-path "users.db"
cocat set-room-owner --email "mary@example.com" --room-id "lab" --db-path "users.db"
```

`add-user-to-room` and `remove-user-from-room` work on existing rooms; removing an owner
is refused, transfer the ownership first with `set-room-owner`. A room ID given to
`create-room` may only contain letters, digits, `_` and `-`.

Then the server can be launched:

```bash
cocat serve --host "127.0.0.1" --port 8000 --update-dir "update_dir" --db-path "users.db"
```

The server listens on `http://127.0.0.1:8000`. Updates are stored in the `update_dir`
directory, with one file per room: the room ID is the last component of the websocket
path, so a client connecting to `ws://127.0.0.1:8000/room/lab` uses room `lab` and the
file `update_dir/lab.y`.

These environment variables change how the server behaves:

| Variable | Effect |
|---|---|
| `COCAT_PROXY_PREFIX` | path prefix the server is served under when a reverse proxy rewrites the URL (default: none) |
| `COCAT_PUBLIC_URL` | origin used in the share links the web UI hands out (default: the host of the request) |
| `COCAT_TRUSTED_PROXIES` | number of reverse proxies in front of the server (default `0`) |
| `COCAT_SECRET` | JWT signing secret, overriding the one stored in the `<db-path>.secret` file |
| `COCAT_SMTP_HOST` | SMTP relay that sends verification, password-reset and invitation emails (default: none, see [Email](#email)) |
| `COCAT_SMTP_PORT` | SMTP port (default `587`, the submission port that upgrades with STARTTLS) |
| `COCAT_SMTP_USERNAME` | SMTP user, when the relay wants one; requires `COCAT_SMTP_PASSWORD` |
| `COCAT_SMTP_PASSWORD` | SMTP password, kept out of the logs and out of `repr` |
| `COCAT_SMTP_STARTTLS` | whether to upgrade the connection with STARTTLS (default `true`) |
| `COCAT_MAIL_FROM` | address the emails come from, required when `COCAT_SMTP_HOST` is set |
| `COCAT_OAUTH_PROVIDERS` | JSON list of OAuth providers, see [ORCID and other OAuth providers](#orcid-and-other-oauth-providers) |

### Web UI

The same command serves a web interface at `http://127.0.0.1:8000/ui/`: same process,
same port, same `fastapiusersauth` cookie as the API, so a browser session works for
both. Sign in with an account an administrator created with `cocat create-user`, or with
one you create yourself on the sign-up page: with an SMTP relay configured the new account
has to confirm its address first, and without one it is usable straight away. The reset
page mails a new password link only when a relay is configured.

| Page | What it does |
|---|---|
| `/ui/signup` | create an account; it joins the room you already have access to as an editor |
| `/ui/verify` | the page the verification link opens |
| `/ui/forgot-password` | ask for a reset link by email |
| `/ui/reset-password` | the page the reset link opens |
| `/ui/rooms` | your rooms, create one, rename or delete the ones you own, leave the others |
| `/ui/rooms/{room_id}` | members and their roles, transfer the ownership, remove a member, invite by email, pending invitations, create and revoke share links |
| `/ui/invitation` | who invites you to which room, and the button that joins it |
| `/ui/account` | change your password |
| `/ui/confirm-email` | give an address to an account that signed in with a provider that hides it |

Until roles are enforced, the UI only hands out the `editor` role: an invitation joins
you as an editor, and there is no role picker.

A share link looks like `http://127.0.0.1:8000/ui/invitation#<token>`: the token is in the
URL fragment, which the browser keeps to itself, so it never reaches a server log. The
origin of those links is `COCAT_PUBLIC_URL` when it is set, otherwise the host of the
request that created them; set it when the server runs behind a proxy. Set
`COCAT_PROXY_PREFIX` the same way for the API: the UI is served under that prefix too.

The login cookie is `Secure`, so browsers only keep it over HTTPS or on `localhost`.

Two proxy details matter for that cookie. Logging in posts a form, and the post is
refused when its `Origin` does not match the `Host` header, so a proxy that rewrites
`Host` to an internal name must preserve the client's `Host` instead. And logging out
clears the cookie but does not revoke the token: the JWT stays valid for up to an hour,
so a cookie copied before logout keeps working until then.

`COCAT_PUBLIC_URL` is an origin (`https://cocat.example.org`), not a path: it is
concatenated with the prefix `COCAT_PROXY_PREFIX` already put in the link.

`COCAT_TRUSTED_PROXIES` tells the per-client rate limiter (10 login attempts per minute)
which address to trust: with the default `0` it uses the socket peer, and behind a proxy
every client shares one bucket. It must be a non-negative integer, otherwise the server
refuses to start.

The JWT signing secret is created in a file next to the database (`<db-path>.secret`) by
whichever command runs first, so sessions survive restarts. Set `COCAT_SECRET` when the
server cannot read or write that file, for instance because the database lives in a
directory the server user cannot modify.

### Email

Set `COCAT_SMTP_HOST` and `COCAT_MAIL_FROM` to send verification, password-reset and
invitation emails. The connection is a plain SMTP submission on port 587, upgraded with
STARTTLS, with a ten-second timeout: implicit TLS on port 465 is not supported. When
`COCAT_SMTP_USERNAME` is set, `COCAT_SMTP_PASSWORD` is required too. A message the relay
refuses never crashes a request: the page says the mail could not be sent, and the account
or invitation it was about survives.

Without a relay the server still runs. Registration skips verification and signs the new
account straight in, invitation and share links stay visible in the UI so you can copy
them by hand, and the reset page has nothing to send. Verification is what gates sign-in
once a relay appears: an unconfirmed account is refused until it confirms, which is why
migration `0003` marks the accounts that predate the mailer as verified and
`cocat create-user` creates verified accounts.

Links carry their token in the URL fragment (`/ui/verify#<token>`), which the browser
keeps to itself, so a token never reaches a server log, a referrer header or a proxy. The
page asks the browser for the fragment over the websocket and clears it from the address
bar afterwards.

### ORCID and other OAuth providers

Providers come from `COCAT_OAUTH_PROVIDERS`, a JSON list:

```json
[
  {
    "name": "orcid",
    "discovery_url": "https://orcid.org/.well-known/openid-configuration",
    "client_id": "APP-XXXXXXXX",
    "client_secret": "XXXXXXXX",
    "verifies_email": false
  }
]
```

`verifies_email` defaults to `false` and `scopes` to `["openid"]`; ORCID has no email
scope, so leave both alone for it. A provider whose discovery document cannot be read at
startup is skipped with a warning naming it, and the rest of the server runs.

Only a provider flagged `verifies_email: true` may hand cocat an address, and even then the
identity is linked onto an existing account only when that account is already verified and
a mailer is configured, so an unverified local account can never be taken over. Without the
flag, or when the provider releases no address at all (ORCID's usual behaviour), the
account gets a placeholder `@placeholder.cocat.org` address and its owner gives a real one
on `/ui/confirm-email`. Nothing is ever mailed to a placeholder.

Set `COCAT_PUBLIC_URL` when providers are configured: the server refuses to start without
it, because both callback URLs are built from it and both have to match what you registered
with the provider:

- `<COCAT_PUBLIC_URL>/ui/oauth/<name>/callback` for the web UI
- `<COCAT_PUBLIC_URL>/auth/<name>/callback` for the REST API

An account that signs in through a provider has no cocat password to change on the account
page: change it at the provider. Because ORCID usually hides the address, an ORCID login
creates its own account even when you already have one; confirm the same address on both
and nothing merges them by itself.

### Rooms and invitations

Rooms have members with one of three roles: `owner`, `editor` and `viewer` (viewers are
not enforced yet, so the API only hands out `editor`). Every user owns their personal
room.

A logged-in user manages their rooms through `/api`, with the same `fastapiusersauth`
cookie as the rest of the server:

| Method and path | What it does |
|---|---|
| `GET /api/me` | the logged-in user |
| `GET /api/rooms` | the rooms you belong to, with your role |
| `POST /api/rooms` | create a room (you own it) |
| `PATCH /api/rooms/{room_id}` | rename a room (owner) |
| `DELETE /api/rooms/{room_id}` | delete a room (owner) |
| `GET /api/rooms/{room_id}/members` | list the members (any member) |
| `PATCH /api/rooms/{room_id}/members/{user_id}` | change a member role (owner), `editor` only for now |
| `DELETE /api/rooms/{room_id}/members/{user_id}` | remove a member, or leave (owner or self) |
| `POST /api/rooms/{room_id}/owner` | transfer the ownership (owner) |
| `POST /api/rooms/{room_id}/invitations` | create an invitation (owner) |
| `GET /api/rooms/{room_id}/invitations` | list the valid invitations (owner) |
| `DELETE /api/rooms/{room_id}/invitations/{invitation_id}` | revoke an invitation (owner) |
| `POST /api/invitations/preview` | preview an invitation (no login needed) |
| `POST /api/invitations/accept` | accept an invitation (login needed) |

An invitation carries a role and an expiry date (14 days by default) and, if it targets
an e-mail address, a single use. Its token is only ever sent in the request body, never
in a URL:

```py
import httpx

token = "..."
httpx.post("http://localhost:8000/api/invitations/preview", json={"token": token})
```

Accepting an invitation adds you to the room with the role it carries; existing members
keep their highest role.
