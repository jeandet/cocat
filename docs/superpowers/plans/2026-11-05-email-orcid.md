# Email and ORCID, phase 3: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the self-service rooms app a real account lifecycle: an SMTP sender configured by environment variables, open self-registration with email verification, password reset by email, invitation emails, and ORCID login in the web UI — the whole phase 3 of the design.

**Architecture:** Two new app-level modules carry the new capabilities, so the REST API and the NiceGUI pages keep sharing one implementation: `app/mail.py` (a `Mailer` protocol, a `Mail` SMTP implementation read from the environment, and the message templates) and `app/accounts.py` (verification and reset link building plus `start_verification`, the one place that decides "verification skipped" when no SMTP is configured). `app/oauth.py` turns a JSON provider list into httpx_oauth `OpenID` clients wrapped so the email a provider asserts is only ever used when the provider is flagged as verifying emails. Tokens travel in URL fragments consumed by UI pages (never in a path or a query) and in POST bodies on the stock fastapi-users routes; no token is ever logged. Everything is wired through the existing `CocatApp` → `UIContext` seam, and every test runs with a recording mailer and fake OAuth clients, so no test opens a socket.

**Tech Stack:** Python ≥ 3.10, FastAPI, fastapi-users 15 (`fastapi-users[sqlalchemy,oauth]`, which brings `httpx-oauth`), NiceGUI 3.19, SQLAlchemy 2 + alembic, stdlib `smtplib`/`email.message` for SMTP (no new mail dependency), pytest + anyio.

**Spec:** `docs/superpowers/specs/2026-11-04-self-service-rooms-design.md`, phase 3 (lines 132-150, 173-180, 196-204).

**Review history:** First draft, written after checking the libraries actually installed in `.venv` on 2026-11-05: `fastapi-users 15.0.5` is present but **`httpx-oauth` is not** (the `oauth` extra is missing, and `fastapi_users.router.oauth` fails to import without it), and neither `aiosmtplib` nor `emails` is installed. The plan therefore adds the `oauth` extra in the task that needs it and builds mail on the standard library. fastapi-users 15 exposes **no GET token-in-path route** for verification or reset, which is what makes the fragment-link design possible without fighting the library.

---

## Global Constraints

- **Python ≥ 3.10.** CI runs 3.10-3.14. No `except*`/`ExceptionGroup`, no `enum.StrEnum`, no `typing.Self`, no `asyncio.timeout`, no `datetime.UTC`. `X | Y` annotations and `match` are fine.
- **100 % coverage** and no new `# pragma: nocover`. Test helpers are measured too, so every helper must be used.
- **No test may open a network connection.** Mail goes through a `RecordingMail` test double; OAuth goes through fake clients; `smtplib.SMTP` is monkeypatched when the real `Mail.send` is exercised. Never construct `httpx_oauth.clients.openid.OpenID` in a test (its `__init__` does a blocking discovery request); production code builds it at startup only.
- **Tokens never leak.** An invitation, verification or reset token appears only (a) in the fragment of the link we hand out, (b) in a POST body, (c) in the raw token returned by a service function to its caller. Never in a path, a query string, a log line, a notification — except the sender's own freshly created link, which the UI shows to be copied when no SMTP is configured.
- **The server works with no SMTP configured.** That is the default, and the default in the whole test suite: `mail=None` means verification is skipped and links are shown in the UI.
- **One rate limiter.** Every new public entry point (register, forgot-password, reset, sign-up, provider login) goes through the existing `check_limit`.
- **The UI calls services, never our own HTTP.** Pages use `manager`, `rooms`/`invitations` services and `RoomActions` directly; the stock fastapi-users routes stay the API/plugin twin.
- **Secrets stay in the environment.** The SMTP password and the OAuth client secrets have no defaults, are never logged, and are `repr=False` in the dataclasses that hold them.
- **`pyproject.toml`**: the `server` extra becomes `fastapi-users[sqlalchemy,oauth] >=15.0.1,<16.0.0`; `nicegui` and the rest stay as they are. The version bump belongs to the release task only.
- **Run full checks before each commit**: `export PATH=$PWD/.venv/bin:$PATH; coverage run -m pytest tests -q && coverage report --fail-under=100 && ruff check src tests && ruff format --check src tests && mypy src tests` (~2 m 15 s, so use a generous timeout). One run at a time.
- **Never stage `.coverage`, `__pycache__`, `.venv`, `*.secret`.** Stage explicit paths, never `git add .`.

## Review Focus

1. **Nobody is locked out the day SMTP is switched on.** Existing users are grandfathered by migration, the CLI creates verified users, and the login gate only refuses unverified accounts when a mailer is configured. Test owner: Task 1.
2. **The login gate is one gate.** `UserManager.authenticate` is the single override; the REST `/auth/jwt/login` and the UI form must both honour it, and neither may be quietly bypassed by the OAuth path. Test owner: Tasks 1, 5.
3. **Tokens never reach a path, a query, a log or a notification.** Fragments are read client-side; the stock routes take tokens in bodies; the mailer never logs. Test owner: Tasks 2, 3.
4. **No account takeover through OAuth.** A provider's asserted email is used only when the provider is flagged `verifies_email`; otherwise the account gets a `.invalid` placeholder and the user confirms a real address. Test owner: Tasks 5, 6.
5. **The ORCID-hides-the-email case is handled, not special-cased.** No email, unverified email and untrusted email all end on the same confirm-email page. Test owner: Tasks 5, 6.
6. **Registration abuse is bounded.** `/auth/register` is public now, so it is rate limited in both front ends, and verification is what makes a created account usable. Test owner: Task 2.
7. **Email invitations do not regress the no-SMTP path.** The link stays visible in the room page when no mailer is configured. Test owner: Task 4.
8. **Tests stay offline and fast.** No SMTP, no discovery request, no ORCID. Test owner: Tasks 1, 5.

---

## File map

| File | Responsibility |
| --- | --- |
| `src/cocat/app/mail.py` | `Mailer` protocol, `Mail` (SMTP + `from_env`), message templates, `MailError` |
| `src/cocat/app/accounts.py` | Verification and reset link building, `start_verification`, placeholder email rules |
| `src/cocat/app/oauth.py` | `OAuthProviderConfig`, `providers_from_env`, `build_client`, `TrustedEmailClient`, `make_oauth_router`, `placeholder_email` |
| `src/cocat/app/users.py` | `UserManager` mail hooks, `authenticate` gate, `get_by_oauth_account`, `make_user_manager(..., mail)` |
| `src/cocat/app/db.py` | `OAuthAccount`, `User.oauth_accounts` |
| `src/cocat/app/actions.py` | `RoomActions.mail`, `RoomActions.invite` (create + mail) |
| `src/cocat/app/routes.py` | Invitations POST goes through `RoomActions.invite` |
| `src/cocat/app/app.py` | `mail`/`oauth` arguments, public register, OAuth routers, `UIContext.mail`/`oauth` |
| `src/cocat/app/migrations/versions/0003_verify_existing_users.py` | Grandfather existing users as verified |
| `src/cocat/app/migrations/versions/0004_oauth_account.py` | `oauth_account` table |
| `src/cocat/cli.py` | `create-user` creates verified users; `serve` builds the mailer from the environment |
| `src/cocat/ui/token.py` | Fragment token reading shared by every token page |
| `src/cocat/ui/signup.py` | Sign-up page and `POST /ui/signup` |
| `src/cocat/ui/verify.py` | Email verification page |
| `src/cocat/ui/reset.py` | Forgot-password page, `POST /ui/forgot-password`, reset page |
| `src/cocat/ui/oauth.py` | Browser OAuth login and redirecting callback |
| `src/cocat/ui/confirm_email.py` | Confirm-an-email page for placeholder accounts |
| `src/cocat/ui/{__init__,context,layout,login,room,account,invitation}.py` | Registration, context, header hand-off, provider buttons, invite-by-email, confirm-email link |
| `pyproject.toml` | `oauth` extra, version `0.14.0` |
| `tests/utils.py` | `RecordingMail`, `mails_to`, fake OAuth client, `ui_mail` support |
| `tests/conftest.py` | `ui_mail`, `oauth_app` fixtures |
| `tests/test_mail.py`, `tests/test_accounts.py`, `tests/test_migrations.py` | Task 1 |
| `tests/test_routes.py`, `tests/test_login_gate.py` | Task 1 |
| `tests/test_ui_signup.py`, `tests/test_ui_verify.py` | Task 2 |
| `tests/test_ui_reset.py` | Task 3 |
| `tests/test_actions.py`, `tests/test_ui_room.py` | Task 4 |
| `tests/test_oauth.py`, `tests/test_ui_oauth.py` | Task 5 |
| `tests/test_ui_confirm_email.py` | Task 6 |
| `docs/usage.md`, `CHANGELOG.md` | Task 7 |

---

### Task 1: Email sender, verification and the login gate

**Files:**
- Create: `src/cocat/app/mail.py`, `src/cocat/app/accounts.py`, `src/cocat/app/migrations/versions/0003_verify_existing_users.py`, `tests/test_mail.py`, `tests/test_accounts.py`, `tests/test_login_gate.py`
- Modify: `src/cocat/app/users.py`, `src/cocat/app/app.py`, `src/cocat/cli.py`, `tests/conftest.py`, `tests/utils.py`, `tests/test_migrations.py`

**Interfaces:**
- `class MailError(Exception)` — raised for a refused recipient or an SMTP failure.
- `class Mailer(Protocol)`: `async def send(self, to: str, subject: str, body: str) -> None`.
- `@dataclass(frozen=True) class Mail:` fields `host: str`, `sender: str`, `port: int = 587`, `username: str | None = None`, `password: str | None = field(default=None, repr=False)`, `starttls: bool = True`. `@classmethod def from_env(cls, environ: Mapping[str, str] = os.environ) -> Mail | None` reads `COCAT_SMTP_HOST`, `COCAT_SMTP_PORT`, `COCAT_SMTP_USERNAME`, `COCAT_SMTP_PASSWORD`, `COCAT_SMTP_STARTTLS`, `COCAT_MAIL_FROM`; returns `None` when `COCAT_SMTP_HOST` is unset, raises `ValueError("COCAT_MAIL_FROM is required when COCAT_SMTP_HOST is set")` when the sender is missing, and treats `COCAT_SMTP_STARTTLS` as off only for `0`/`false`/`no` (case-insensitive).
- `Mail.send`: refuses recipients ending in `.invalid` with `MailError("Cannot send mail to a placeholder address")` before touching SMTP, builds an `EmailMessage` (`From`, `To`, `Subject`, `set_content`), and runs the blocking part with `anyio.to_thread.run_sync`; an `smtplib.SMTPException` or `OSError` becomes `MailError(str(error))` with no token and no credentials in the message. `_deliver(message)`: `smtplib.SMTP(host, port)` as a context manager, `starttls()` when `starttls`, `login(username, password)` when `username`, `send_message(message)`.
- `def verification_mail(link: str) -> tuple[str, str]` returning `(subject, body)`; the body states the link expires in an hour and quotes it on its own line.
- `async def start_verification(manager: UserManager, user: User, request: Request, mail: Mailer | None) -> None` — with `mail is None`, `await manager.update(UserUpdate(is_verified=True), user)`; otherwise `await manager.request_verify(user, request)` (which calls `on_after_request_verify`).
- `def verification_link(request: Request, token: str) -> str` — `absolute_url(request, f"/verify#{token}")` (fragment only).
- `UserManager.__init__(user_db, mail: Mailer | None = None)`; `authenticate(credentials)` returns `None` when `self.mail is not None and user is not None and not user.is_verified`, otherwise delegates to `super().authenticate` (the docstring says an unverified account exists but may not sign in until it proves the address, and that the gate is open when no mail can be sent).
- `UserManager.on_after_request_verify(user, token, request)`: when `self.mail` is set, `await self.mail.send(user.email, *verification_mail(verification_link(request, token)))`; nothing else (no printing of the token; the user id stays as today's log-free stand-in is removed).
- `make_user_manager(user_db, secret, mail: Mailer | None = None)`; `get_backend(db, secret, mail)`; `@dataclass Backend` gains `mail: Mailer | None`.
- `CocatApp(update_dir, db_path="./test.db", *, mail: Mailer | None = None, oauth: ... | None = None)`; `cli._serve` passes `Mail.from_env()` so the environment is read in exactly one place.
- Migration `0003`: `upgrade` runs `op.execute('UPDATE "user" SET is_verified = 1')`, `downgrade` is a no-op with a comment saying the previous verification state cannot be recovered.
- `cli._create_user` creates the user with `is_verified=True` (an administrator vouches for the address).

- [ ] **Step 1: Write failing tests.** `tests/test_mail.py`: `from_env` returns `None` without `COCAT_SMTP_HOST`; parses a full environment; defaults to port 587 with STARTTLS on; `COCAT_SMTP_STARTTLS=0` turns it off; a missing `COCAT_MAIL_FROM` raises; `send` on a `.invalid` recipient raises `MailError` without SMTP; `send` through a monkeypatched `smtplib.SMTP` (a fake class recording `starttls`/`login`/`send_message`) checks the delivered `From`/`To`/`Subject`/body, that `starttls` ran, and that `login` runs only with a username; a fake SMTP raising `smtplib.SMTPException` surfaces as `MailError`; `verification_mail` carries the link. `tests/test_accounts.py`: `start_verification` with `mail=None` verifies the user in the database; with a `RecordingMail` it sends exactly one message to the user with a `/verify#` link and leaves `is_verified` false. `tests/test_login_gate.py` (new, `app_client`-based but with a mailer): an unverified user gets 400 from `POST /auth/jwt/login` and `?error=credentials` from `POST /ui/login`, while a verified user logs in on both; with no mailer an unverified user logs in. `tests/test_migrations.py`: a database with an unverified user, migrated to head, has that user verified.

- [ ] **Step 2: Run the new tests.** `export PATH=$PWD/.venv/bin:$PATH; pytest tests/test_mail.py tests/test_accounts.py tests/test_login_gate.py -q`. Expected: FAIL, `ModuleNotFoundError: cocat.app.mail` and, once that exists, `TypeError` for the new `mail` argument.

- [ ] **Step 3: Implement.** `RecordingMail` and `mails_to` live in `tests/utils.py` (`@dataclass class RecordingMail: messages: list[tuple[str, str, str]] = field(default_factory=list)` with `async def send(self, to, subject, body)`, plus `def mails_to(mail: RecordingMail, email: str) -> list[tuple[str, str, str]]` filtering by recipient — both used from Task 2 on). Add a `mail`-carrying `app_client` variant to `tests/conftest.py` only if `test_login_gate.py` cannot build its own app: prefer building the `TestClient` in the test module with `clean_nicegui()` and `CocatApp(update_dir, db_path, mail=RecordingMail())`.

- [ ] **Step 4: Run the new tests, then the full suite** (all pass, coverage 100 %).

- [ ] **Step 5: Commit** `git commit -m "Add the email sender and account verification"` (stage the explicit paths above).

---

### Task 2: Sign-up and email verification pages

**Files:**
- Create: `src/cocat/ui/token.py`, `src/cocat/ui/signup.py`, `src/cocat/ui/verify.py`, `tests/test_ui_signup.py`, `tests/test_ui_verify.py`
- Modify: `src/cocat/ui/invitation.py`, `src/cocat/ui/login.py`, `src/cocat/ui/__init__.py`, `src/cocat/ui/layout.py`, `src/cocat/app/app.py`, `src/cocat/app/users.py`, `tests/conftest.py`

**Interfaces:**
- `ui/token.py`: `FRAGMENT_SCRIPT`, `CLEAR_FRAGMENT_SCRIPT`, `async read_fragment_token() -> str`, `def token_from_text(text: str) -> str` — moved unchanged out of `ui/invitation.py`, which imports them from here (docstring keeps the "a token in a path or query ends up in access logs" rationale).
- `layout.hand_off(target: str) -> RedirectResponse` — `context.client.delete()` then `RedirectResponse(target, status_code=303)`; the one way a page leaves while a client exists (the sign-up page uses it after auto-login; `require_user` keeps its own delete-then-307).
- `ui/signup.py`: `register(ctx)` adds `@ui.page("/signup")` and `POST /ui/signup` (plain route on the host app, before the mount, like `/ui/login`). `signup_form(action, error)` renders escaped email/password inputs marked `signup-email`, `signup-password` and a `Create account` button; `MESSAGES` gains `taken: "That email is already registered."` and reuses `too_many`. The route: `same_site` → `ctx.check_limit` (429 → `?error=too_many`) → `manager.create(UserCreate(email=..., password=...))` catching `UserAlreadyExists` (→ `?error=taken`) → `start_verification(...)` → with `ctx.mail is None` the account is already verified, so log in with the same cookie machinery `auth.post_login` uses and `hand_off(ui_url(request, "/rooms"))`; with a mailer, `hand_off(ui_url(request, "/login") + "?created=1")`.
- `ui/login.py`: the login page gains `Sign up` (marker `nav-signup`) and shows `Check your inbox to verify your address.` when `?created=1` (`MESSAGES["created"]`).
- `ui/verify.py`: `@ui.page("/verify")`, `require_user`-style guard is **not** used (the link may be opened signed out) — read the fragment with `read_fragment_token`, clear it, show the result of `manager.verify(token, request)` in a `@ui.refreshable` (`This address is verified.`, `This verification link is not valid anymore.`, `This address is already verified.`), and a link to the login page when signed out.
- `app/app.py`: the register router loses `dependencies=[Depends(current_superuser)]` and gains the limiter via `with_route_dependency(register_router, "/register", check_limit)`.
- `UserManager.on_after_register` keeps creating the personal room; it no longer prints the user id (the id is still available to operators through `cocat get-user`).

- [ ] **Step 1: Write failing tests.** `tests/test_ui_signup.py`: sign-up with no mailer creates the account, logs the visitor in and lands on `/ui/rooms` (a room list marker is visible, the cookie is set); with a `ui_mail` fixture the visitor stays signed out, is sent one message to their address whose body contains `/verify#`, and `/ui/login?created=1` shows the inbox message; a duplicate email shows `That email is already registered.`; the eleventh attempt within a minute gets `?error=too_many`; a foreign `Origin` gets 403; a `.invalid`-style address is refused by the mailer (no crash, the account still exists unverified) — assert on the notification, never on a token. `tests/test_ui_verify.py`: a signed-in user opening `/ui/verify` with a `javascript_rules` token (regex anchored at `window\.location\.hash`) sees `This address is verified.` and can then log in by password; an invalid token shows the not-valid message; a second visit with the same token shows the already-verified message. Also assert the REST `POST /auth/register` is now open (no superuser cookie) and rate limited.

- [ ] **Step 2: Run them.** `pytest tests/test_ui_signup.py tests/test_ui_verify.py -q`. Expected: FAIL (404 on `/ui/signup`, `ImportError` for `cocat.ui.token`).

- [ ] **Step 3: Implement** the modules, register them in `register_pages`, and move the fragment helpers.

- [ ] **Step 4: Run the new tests, then the full suite** (all pass, coverage 100 %).

- [ ] **Step 5: Commit** `git commit -m "Add sign-up and email verification pages"`.

---

### Task 3: Password reset pages

**Files:**
- Create: `src/cocat/ui/reset.py`, `tests/test_ui_reset.py`
- Modify: `src/cocat/ui/login.py`, `src/cocat/ui/__init__.py`, `src/cocat/app/mail.py`, `src/cocat/app/accounts.py`, `src/cocat/app/users.py`

**Interfaces:**
- `mail.reset_mail(link) -> tuple[str, str]`; `accounts.reset_link(request, token) -> str` = `absolute_url(request, f"/reset-password#{token}")`.
- `UserManager.on_after_forgot_password(user, token, request)` sends `reset_mail(reset_link(request, token))` when a mailer is configured and does nothing otherwise (the UI explains the no-SMTP case).
- `ui/reset.py`: `@ui.page("/forgot-password")` with an `email` input (`forgot-email`) and `Send reset link` (`forgot-send`), posting to `POST /ui/forgot-password`; the route is rate limited and always redirects to `/forgot-password?sent=1`, whether or not the address exists (no account enumeration), calling `manager.forgot_password(user, request)` only for a known, verified lookup.
- `@ui.page("/reset-password")`: reads the fragment with `read_fragment_token`, clears it, renders two password inputs (`reset-password`, `reset-repeat`) plus `Change password` (`reset-submit`), and calls `manager.reset_password(token, password, request)` directly; `InvalidResetPasswordToken` → `This reset link is not valid anymore.`, success → `hand_off(ui_url(request, "/login") + "?reset=1")`, and the login page shows `Your password changed. Sign in again.` for that flag. Mismatched repeats are refused in-page before the manager call.
- The reset link is never in a path or a query; the stock `POST /auth/reset-password` (`{token, password}`) stays the API twin.

- [ ] **Step 1: Write failing tests** (`tests/test_ui_reset.py`, using `ui_mail`): requesting a reset sends one message whose body has `/reset-password#`; the flow end to end — request, extract the token with `token_from_text` from the recorded body, open `/ui/reset-password` with that token through `javascript_rules`, submit a new password, land on the login page, and log in with the new password (the old one no longer works); an unknown address sends nothing and still shows `?sent=1`; a mismatched repeat changes nothing; an invalid token shows the not-valid message; the route is rate limited; a foreign `Origin` is refused.

- [ ] **Step 2: Run them.** `pytest tests/test_ui_reset.py -q`. Expected: FAIL (404, no reset mail).

- [ ] **Step 3: Implement** and register the pages.

- [ ] **Step 4: Run the new tests, then the full suite** (all pass, coverage 100 %).

- [ ] **Step 5: Commit** `git commit -m "Add the password reset pages"`.

---

### Task 4: Invitation emails

**Files:**
- Modify: `src/cocat/app/actions.py`, `src/cocat/app/routes.py`, `src/cocat/app/mail.py`, `src/cocat/app/accounts.py`, `src/cocat/ui/room.py`, `tests/test_actions.py`, `tests/test_ui_room.py`, `tests/test_routes.py`

**Interfaces:**
- `mail.invitation_mail(room_name: str, inviter: str, link: str) -> tuple[str, str]`.
- `@dataclass(frozen=True) RoomActions:` gains `mail: Mailer | None = None` and `async invite(session, room_id, actor, role, email, request) -> tuple[Invitation, str]` — `create_invitation(...)` then, when `self.mail` is set, `await self.mail.send(email, *invitation_mail(room.name, actor.email, absolute_url(request, f"/invitation#{token}")))`. Errors from the mailer are turned into a `RoomError("The invitation is created but the email could not be sent")` so the UI can say so while the invitation still exists.
- `make_api_router` builds `RoomActions(connections, update_dir, mail)`; the invitations `POST` route calls `actions.invite(...)` when the body has an `email`, and keeps returning the link in `InvitationCreated{id, token}` as today.
- `ui/room.py`: the owner's invitations block gains an `Invite by email` input (`invite-email`) and `Send invitation` button (`invite-send`) inside the same refreshable; on success it notifies `Invitation sent to <email>`, and when `ctx.mail is None` it fills the share-link input with the created link instead (the copy-by-hand path from phase 2 keeps working).

- [ ] **Step 1: Write failing tests.** `tests/test_actions.py`: `invite` with a `RecordingMail` sends one message to the address with the room name and a `/invitation#` link, and creates the invitation; without a mailer it sends nothing and still returns the token; a mailer raising `MailError` surfaces as `RoomError` while the invitation exists. `tests/test_ui_room.py`: an owner sending an invitation to an address sees `Invitation sent to <address>` and the invitation row appears; with a mailer configured the raw token does not appear in any notification; with no mailer the share-link input is filled instead. `tests/test_routes.py`: `POST /api/rooms/{id}/invitations` with an `email` sends a mail through the injected mailer.

- [ ] **Step 2: Run them.** `pytest tests/test_actions.py tests/test_ui_room.py tests/test_routes.py -q`. Expected: FAIL (`TypeError` on the new `invite` signature / missing marker).

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run the new tests, then the full suite** (all pass, coverage 100 %).

- [ ] **Step 5: Commit** `git commit -m "Email room invitations"`.

---

### Task 5: ORCID login

**Files:**
- Create: `src/cocat/app/oauth.py`, `src/cocat/ui/oauth.py`, `src/cocat/app/migrations/versions/0004_oauth_account.py`, `tests/test_oauth.py`, `tests/test_ui_oauth.py`
- Modify: `src/cocat/app/db.py`, `src/cocat/app/users.py`, `src/cocat/app/app.py`, `src/cocat/ui/context.py`, `src/cocat/ui/login.py`, `src/cocat/ui/__init__.py`, `tests/conftest.py`, `tests/utils.py`, `pyproject.toml`

**Interfaces:**
- `app/db.py`: `class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base)` (table `oauth_account`), `User.oauth_accounts: Mapped[list[OAuthAccount]] = relationship(lazy="joined", cascade="all, delete-orphan")`; `make_user_manager` passes `oauth_account_table=OAuthAccount` to `SQLAlchemyUserDatabase`; `UserManager.get_by_oauth_account(oauth_name, account_id)` returns the user or raises `UserNotExists`.
- Migration `0004` creates `oauth_account` (columns as the mixin defines them) with the `user_id` foreign key and its index; `downgrade` drops it.
- `app/oauth.py`: `class OAuthProviderConfig(BaseModel)` — `name: str`, `discovery_url: str` (aliased `discovery_url`), `client_id: str`, `client_secret: SecretStr`, `verifies_email: bool = False`, `scopes: list[str] | None = None`; `providers_from_env(environ=os.environ) -> list[OAuthProviderConfig]` parses `COCAT_OAUTH_PROVIDERS` as a JSON list (empty/unset → `[]`; invalid JSON or a missing field raises `ValueError` naming the variable); `build_client(config) -> OpenID` (`openid_configuration_endpoint=config.discovery_url`, `name=config.name`, `base_scopes=config.scopes`); `class TrustedEmailClient` wrapping any client with `name`, `async get_authorization_url`, `async get_access_token`, `async get_id_email(token) -> tuple[str, str]` — returns `(account_id, email)` when `config.verifies_email` and `email` is not `None`, otherwise `(account_id, placeholder_email(config.name, account_id))` (docstring: an unverified asserted address must never reach an account, so it is replaced by a placeholder the user later confirms); `placeholder_email(provider, account_id) -> str` = `f"{account_id}@{provider}.invalid"`; `is_placeholder(email) -> bool` = `email.endswith(".invalid")`; `make_oauth_router(clients, backend, get_user_manager, secret)` gives one router per provider via `get_oauth_router(client, backend, get_user_manager, secret, redirect_url=f"{public}/auth/{name}/callback", associate_by_email=config.verifies_email, is_verified_by_default=config.verifies_email)` (stock routes, so `/auth/{name}/authorize` and `/auth/{name}/callback` stay the API twin); `build_clients(providers) -> list[tuple[OAuthProviderConfig, TrustedEmailClient]]`; building any client requires `COCAT_PUBLIC_URL` (a redirect URI that differs from what is registered at the provider silently fails).
- `ui/oauth.py`: `register(ctx)` adds `GET /ui/oauth/{name}/login` and `GET /ui/oauth/{name}/callback` on the host app before the mount. Login: look the client up by name (404 when unknown), `csrf = generate_csrf_token()`, `state = generate_state_token({CSRF_TOKEN_KEY: csrf}, ctx.backend.secret)`, `client.get_authorization_url(absolute_url(request, f"/oauth/{name}/callback"), state, config.scopes)`, set the CSRF cookie with the same flags fastapi-users uses, 307 to the provider. Callback: `OAuth2AuthorizeCallback(client, redirect_url=absolute_url(request, f"/oauth/{name}/callback"))` as a dependency, `decode_jwt(state, secret, [STATE_TOKEN_AUDIENCE])`, `secrets.compare_digest` against the CSRF cookie, `manager.oauth_callback(name, token["access_token"], account_id, email, expires_at, refresh_token, request, associate_by_email=config.verifies_email, is_verified_by_default=config.verifies_email)`, then the login cookie via `with_cookies` and 303 to `/ui/rooms`, or to `/ui/confirm-email` when the account email is a placeholder; `UserAlreadyExists` → 303 `/ui/login?error=exists`; a bad state or CSRF → 400. `login.py` renders one button per provider (marker `oauth-<name>`) linking to that login route.
- `UIContext` gains `mail: Mailer | None` and `providers: list[tuple[OAuthProviderConfig, TrustedEmailClient]]`; `CocatApp` builds them once and passes them to both `mount_ui` and the API routers.
- `pyproject.toml`: `fastapi-users[sqlalchemy,oauth]`.

- [ ] **Step 1: Write failing tests.** `tests/utils.py` gains `@dataclass class FakeOAuthClient` (`name`, `email: str | None`, `async get_authorization_url(redirect_uri, state, scopes=None)` returning `f"https://provider.test/authorize?state={state}"`, `async get_access_token(code, redirect_uri, code_verifier=None)` returning a fixed `{"access_token": "token", "expires_at": ...}`, `async get_id_email(token)` returning `(self.account_id, self.email)`). `tests/test_oauth.py`: `providers_from_env` on empty/unset is `[]`, on a valid JSON list yields the fields, on invalid JSON and on a missing field raises `ValueError`; `placeholder_email`/`is_placeholder`; `TrustedEmailClient.get_id_email` keeps the email for `verifies_email=True` and replaces it otherwise (including the `email is None` case); `make_oauth_router` yields `/authorize` returning the authorization URL plus the CSRF cookie for the fake client. `tests/test_ui_oauth.py` (a fixture building `CocatApp(..., oauth=[(config, FakeOAuthClient(...))])` inside `clean_nicegui`/`ui_simulation`): the login page lists a button per provider; `GET /ui/oauth/orcid/login` answers 307 with `Location` on the provider and sets the CSRF cookie; the callback with the matching state/CSRF sets the login cookie and lands on `/ui/rooms`; a provider with `verifies_email=True` logging in with an existing account's email reaches that same account (no second user); a provider with `verifies_email=False` and the same email creates a placeholder account and lands on `/ui/confirm-email`; the callback without the CSRF cookie answers 400; the second login reuses the stored OAuth account (one user, no duplicate); an account already holding that email under `verifies_email=False` is refused with `?error=exists`.

- [ ] **Step 2: Run them.** `pytest tests/test_oauth.py tests/test_ui_oauth.py -q`. Expected: FAIL (`ModuleNotFoundError: httpx_oauth`, then `cocat.app.oauth`). Install the extra first (`uv pip install -p .venv -e '.[server]' --group test`) so the failure is the missing module, not the dependency.

- [ ] **Step 3: Implement** the model, migration, manager override, `app/oauth.py`, `ui/oauth.py`, the login buttons and the wiring.

- [ ] **Step 4: Run the new tests, then the full suite** (all pass, coverage 100 %).

- [ ] **Step 5: Commit** `git commit -m "Add ORCID login"`.

---

### Task 6: Let OAuth users confirm their email

**Files:**
- Create: `src/cocat/ui/confirm_email.py`, `tests/test_ui_confirm_email.py`
- Modify: `src/cocat/ui/account.py`, `src/cocat/ui/__init__.py`, `src/cocat/app/accounts.py`

**Interfaces:**
- `@ui.page("/confirm-email")` requiring a signed-in user: an `email` input (`confirm-email`) and `Confirm` (`confirm-email-send`). On submit: refuse an empty or `.invalid` address (`Enter an email address`, `That address cannot receive mail`), `await manager.update(UserUpdate(email=address), user)` — fastapi-users resets `is_verified` on an email change — then `start_verification(...)`; with a mailer the page says `Check your inbox to verify <address>.`, without one it verifies immediately and says `Your address is confirmed.`
- `account.py`: when `is_placeholder(user.email)` the page shows the placeholder as `No email address yet` and a `Confirm your email` link (marker `nav-confirm-email`); otherwise it keeps showing the address.

- [ ] **Step 1: Write failing tests.** A placeholder account (created through `manager.oauth_callback` with a fake client) sees `No email address yet` and the confirm link; confirming a real address with a mailer sends one message and leaves the account unverified until the link is used (open the recorded `/verify#` link through `javascript_rules` and check the account is then verified and can log in by password); confirming without a mailer verifies immediately; a `.invalid` address and an empty one are refused; the account page shows the real address after confirmation.

- [ ] **Step 2: Run them.** `pytest tests/test_ui_confirm_email.py -q`. Expected: FAIL (404, `ImportError` for `is_placeholder`).

- [ ] **Step 3: Implement.**

- [ ] **Step 4: Run the new tests, then the full suite** (all pass, coverage 100 %).

- [ ] **Step 5: Commit** `git commit -m "Let OAuth users confirm their email"`.

---

### Task 7: Document email and ORCID login and release 0.14.0

**Files:**
- Modify: `docs/usage.md`, `CHANGELOG.md`, `pyproject.toml`

**Interfaces:** none — documentation and the version bump.

- [ ] **Step 1: Write the docs.** `docs/usage.md`: a `### Email` subsection under `### Web UI` covering the `COCAT_SMTP_*`/`COCAT_MAIL_FROM` variables (table rows: `COCAT_SMTP_HOST`, `COCAT_SMTP_PORT`, `COCAT_SMTP_USERNAME`, `COCAT_SMTP_PASSWORD`, `COCAT_SMTP_STARTTLS`, `COCAT_MAIL_FROM`), the no-SMTP behaviour ("verification is skipped, links are shown in the UI"), what verification means for login once SMTP is on, and the reset flow; a `### ORCID and other OAuth providers` subsection with the `COCAT_OAUTH_PROVIDERS` JSON shape (`[{name, discovery_url, client_id, client_secret, verifies_email, scopes}]`), the note that `COCAT_PUBLIC_URL` must match the redirect URI registered at the provider (`<public>/ui/oauth/<name>/callback` and `<public>/auth/<name>/callback`), that the ORCID client is registered on orcid.org with no federation agreement, and that a provider only links to an existing account by email when `verifies_email` is true — otherwise the user confirms an address after the first login.
- [ ] **Step 2: CHANGELOG.** `## 0.14.0` listing: SMTP sender from environment variables; open registration with email verification; password reset; invitation emails; ORCID/OAuth login with `verifies_email` linking rules and the confirm-email page; migration `0003` (existing users grandfathered) and `0004` (`oauth_account`); `fastapi-users[sqlalchemy,oauth]` in the server extra.
- [ ] **Step 3: Version.** `pyproject.toml` `version = "0.14.0"`.
- [ ] **Step 4: Run the full suite** (docs do not change coverage, but the release must be green).
- [ ] **Step 5: Commit** `git commit -m "Document email and ORCID login and release 0.14.0"`.

---

## After this plan

- Phase 4 (roles) can now rely on verified accounts: a viewer role and its enforcement are the next slice, and the UI can start offering it.
- Deferred on purpose, worth tracking: an optional email-domain allow-list (`COCAT_ALLOWED_EMAIL_DOMAINS`) for open registration; explicit "link this ORCID to my account" from the account page (fastapi-users' `get_oauth_associate_router`); per-user quotas (rooms, storage) which the design puts in a later phase.
- `mkdocs build --strict` cannot run in this worktree until the `docs` dependency group is installed; run it wherever docs are built before publishing.
- The SMTP path is exercised only against a fake `smtplib.SMTP`; a real deployment should be smoke-tested once with `COCAT_SMTP_HOST` set, since TLS negotiation and server quirks are the part no test can prove.
