# Version history

## 0.15.0
- A `viewer` is enforced, not just stored: the room websocket drops every document write a
  viewer sends, so a viewer reads the room and receives other members' updates but cannot
  change it. Editors are unaffected.
- A role change closes that member's connections, so a downgrade applies at once instead of
  when the browser reconnects.
- The API accepts `viewer` for a member's role and for an invitation, and the room page
  offers a role picker for each member and for a new invitation or share link.

## 0.14.0
- OAuth logins are hardened the same way the mailer is: a placeholder address can no
  longer be registered (so nobody can pre-claim an ORCID iD), an identity is never linked
  onto an account holding a placeholder, a provider error returns to `/ui/login` with a
  message, the login route is rate limited and refuses a cross-site navigation, and the
  default `scopes` now include `email` so a provider flagged `verifies_email` really
  releases one.
- `/ui/confirm-email` only serves accounts without an address; it refuses a malformed or
  already-owned address with a message, and `COCAT_PUBLIC_URL` is required whenever a
  mailer or a provider is configured rather than falling back to the request's `Host`.

- An SMTP sender configured from `COCAT_SMTP_*` and `COCAT_MAIL_FROM` sends verification,
  password-reset and invitation emails. Without it the server runs unchanged: verification
  is skipped and links stay visible in the UI to copy by hand.
- Registration is open: the web UI has sign-up, verification, forgot-password and
  reset-password pages, and `/auth/register` is public and rate limited. Accounts that
  predate the mailer are marked verified by migration `0003`, and `cocat create-user`
  creates verified accounts.
- Sign-in is refused for accounts that have not confirmed their address, once a mailer is
  configured, through both the API and the UI.
- Email invitations: `RoomActions.invite` creates the invitation and mails the link, and the
  room page has an "Invite by email" row. The API only returns the raw token when no mailer
  is configured, and an invitation whose mail bounced is rolled back rather than left behind.
- `COCAT_PUBLIC_URL` is now required as soon as a mailer is configured, because a mailed link
  must not be built from the `Host` header; the login page also offers to resend the
  verification mail, and every route has its own rate-limit budget.
- Room names containing control characters, and invitation addresses that are not email
  addresses or are provider placeholders, are refused.
- ORCID login through `httpx_oauth` OpenID clients listed in `COCAT_OAUTH_PROVIDERS`, in the
  web UI and through the stock fastapi-users OAuth router. An identity is linked by address
  only for providers flagged `verifies_email` and only onto a verified local account.
- Providers that hide the address (ORCID) get a placeholder `@placeholder.cocat.org`
  account plus a `/ui/confirm-email` page where the owner supplies a real one. `OAuthAccount`
  and migration `0004` store the identities.

## 0.13.0

- Serve a web UI at `/ui/` from `cocat serve`, in the same process as the API and with
  the same login cookie: my rooms, a page per room with its members and invitations, an
  invitation acceptance page and an account page to change your password.
- Pages call the same Python services as the REST API, so the two front ends cannot
  drift apart; room deletion and member removal go through one `RoomActions` object that
  also closes websockets and removes the room file.
- Leave the owner in charge of invitations: the room page creates share links and
  revokes invitations.
- Serve the UI behind a reverse proxy that strips its prefix, and add
  `COCAT_PUBLIC_URL` for the origin of share links.
- Add `nicegui` to the `server` extra.
- Refuse blank room names in the services, not only in the API.

## 0.12.0

- Back up `users.db` before upgrading.
- Migrations run automatically, at server start and from the CLI.
- Email-prefix rooms keep working for existing users.
- Rooms that were shared by a prefix collision (`john@a.org` and `john@b.org` both in
  room `john`) are listed at upgrade time, and can be fixed with
  `cocat set-room-owner`.
- New users get a personal room. Older SciQLop plugins cannot find it until the
  SciQLop collaborative-catalogs plugin is updated.
- Rate-limit login and forgot-password to 10 requests per minute and per client. Behind
  a reverse proxy, set `COCAT_TRUSTED_PROXIES` to the number of proxies so that clients
  are told apart.
- Keep the JWT signing secret across restarts: it is stored in `<db-path>.secret`, or
  supplied with `COCAT_SECRET`.
- Breaking: `Session.connect()` no longer takes a `room_id`, call
  `set_config(room_id=...)` before `log_in`.

## 0.11.0

- Improve credential storing.

## 0.10.0

- Stop server gracefully.
- Use `keyring` for storing passwords.
- Automatically connect users to a room which ID is based on their email.
- Add `GET /room/{room_id}/users` for all users allowed in a room.
- Add `GET /rooms` for current user allowed rooms.

## 0.9.0

- Don't export from `cocat.cli`, because it has features that need the `server` extra.

## 0.8.0

- Add user room permissions (SQLite database is incompatible).
- Add reverse proxy prefix.

## 0.7.0

- Use `rich`'s pretty representations.
- Make `Catalogue` and `Event` iterable.
- Remove `synchronize` and `connect` from user API.
- Make some class methods private.

## 0.6.0

- Use `wiredb`'s v0.7 synchronous clients in user API.

## 0.5.0

- Refactor user API.
- Add support for VOTable import/export.

## 0.4.2

- Add `range` getter/setter and `on_change_range`.

## 0.4.1

- Add support for dynamic catalogues.
- Don't require a transaction for read-only operations.

## 0.4.0

- Add user authentication.
- Remove `client` extra-dependency.

## 0.3.1

- Add `wiredb` to dependencies.

## 0.3.0

- Add high-level user API and CLI.
- Remove models from public API.
- Disable event callback when same origin.

## 0.2.1

- Bump WireDB v0.2.0.

## 0.2.0

- Use file wire from [WireDB](https://github.com/davidbrochart/wiredb).

## 0.1.0

- Initial release.
