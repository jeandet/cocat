# Email and ORCID, phase 3: Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Send mail from cocat (verification, password reset, room invitations) and let people sign in with ORCID, without making either one mandatory.

**Architecture:** Two app-level modules carry the new behaviour, so both front ends share it exactly as they share `rooms.py`: `app/mail.py` (an SMTP sender plus the message templates) and `app/accounts.py` (starting a verification, building the links). `app/oauth.py` registers one provider per entry of `COCAT_OAUTH_PROVIDERS` using fastapi-users' OAuth router and `httpx_oauth`'s `OpenID` client, wrapped so that an email the provider did not verify never reaches an account. The UI keeps calling services directly; only the flows that are HTTP by nature (a link click, an OAuth redirect) are routes, and those routes redirect rather than render. Tokens travel in URL fragments, read by JavaScript and answered through the socket, because fastapi-users 15 has no GET route that takes a token.

**Tech Stack:** Python ≥ 3.10, FastAPI 0.16x, fastapi-users 15 with the `sqlalchemy` and `oauth` extras, `httpx-oauth` 0.17 (not installed yet), stdlib `smtplib`, NiceGUI 3.19, Alembic, SQLite.

**Spec:** `docs/superpowers/specs/2026-11-04-self-service-rooms-design.md`, phase 3 (lines 132-150, 173-180, 196-204).

**Review history:** First draft (commit `9be1fee`) proposed the mail sender, an `authenticate` override as the verification gate, `placeholder_email(...) = f"{account_id}@{provider}.invalid"`, `associate_by_email=config.verifies_email`, `OAuth2AuthorizeCallback` as a dependency, and a `hand_off` helper. An independent review found 22 problems, all verified against the installed libraries and all applied here: the `.invalid` placeholder breaks `UserRead` (pydantic `EmailStr`) and would 500 `/api/me` (finding 1); the provider scopes dropped `openid` (2); `associate_by_email` linked onto an unverified local account, which is an account takeover (3); ORCID usually returns no email at all, so a stock-router flow would never work and `confirm-email` could hit an unhandled `UserAlreadyExists` (4); `build_client` was untestable because `OpenID.__init__` performs a blocking discovery request (5); a provider outage at boot would crash the server (6); the UI callback skipped the `is_active` check (7); `OAuth2AuthorizeCallback` cannot be declared at registration time without a request (8); SMTP failures and the missing timeout were unhandled (9, 10); API registration never verified anyone (11); overriding `authenticate` also broke the account page's current-password check (12); OAuth redirect URLs ignored the proxy prefix (13); `/auth/request-verify-token` was unlimited (14); login copy still said registration is admin-only (15); OAuth users could never change a password (16); three planned tests could not be written (17); three helpers were avoidable (18); the trust rules around provider-asserted emails were undocumented (19); the `oauth_account` migration lacked a unique constraint (20); `httpx-oauth` was unpinned while being imported at module top (21); and the SMTP env parsing accepted nonsense (22).

## Global Constraints

- Python ≥ 3.10 and the CI matrix 3.10-3.14. No `except*`, `StrEnum`, `typing.Self`, `asyncio.timeout`, `datetime.UTC`, tomllib, or PEP 701 f-strings.
- 100 % coverage, no new `# pragma: nocover`, no coverage exclusions. Test helpers are measured too, so every helper added needs a user.
- **No test may touch the network.** A fake `Mailer` records what would be sent; a fake OAuth client returns canned tokens and profiles; `smtplib.SMTP` is monkeypatched where the real `send` runs; `OpenID` is never constructed in a test (its constructor blocks on a discovery request).
- Tokens (verification, reset, invitation) never appear in a path, a query string, a log line, an error message or a notification. They travel in URL fragments and are read with `ui.run_javascript`; the socket answer is a POST body. The one exception is deliberate: with no SMTP configured, a freshly created link is shown in the UI so the operator can copy it.
- The server runs with no SMTP and no OAuth configured. `mail=None` means "verification is skipped, links are shown in the UI"; `oauth=[]` means "the login page has no provider buttons".
- One rate limiter, the existing `check_limit` (10 calls per minute, `RateLimiter(10, 60.0)`), shared by every unauthenticated form: `/auth/jwt/login`, `/auth/register`, `/auth/forgot-password`, `/auth/request-verify-token`, `POST /ui/login`, `POST /ui/signup`, `POST /ui/forgot-password`.
- The UI calls services, never its own HTTP surface, except where the operation is a browser navigation.
- Secrets (`COCAT_SMTP_PASSWORD`, OAuth client secrets) come from the environment, live in `repr=False` fields or `SecretStr`, and are never printed, logged or rendered.
- `pyproject.toml`: the server extra becomes `"fastapi-users[sqlalchemy,oauth] >=15.0.1,<16.0.0"` plus an explicit `"httpx-oauth >=0.17,<0.18"` (the extra's bound is `>=0.13`; `fastapi_users.router.oauth` imports it at module import, so an unexpected version breaks the app, not just the tests). The version bump belongs to Task 6.
- Full gate before each commit: `export PATH=$PWD/.venv/bin:$PATH; coverage run -m pytest tests -q && coverage report --fail-under=100 && ruff check src tests && ruff format --check src tests && mypy src tests`. The full run takes about two minutes twenty; give the shell a timeout of at least 900 s.
- Stage explicit paths. Never commit `__pycache__`, `.coverage`, `.venv`, `*.secret`, temporary databases. Never push.

## Review Focus

1. **Nobody is locked out when SMTP appears.** A deployment that upgrades from 0.13.0 has users with `is_verified = 0`; migration 0003 grandfathers them, `cocat create-user` creates verified users, and the login gate only exists when a mailer is configured. Test owner: Task 1.
2. **The verification gate lives in one place per front end and does not pretend to be `authenticate`.** REST uses `requires_verification=mail is not None`; the UI checks `not user.is_verified` after a successful `authenticate`, so the account page's current-password check keeps working. Test owner: Tasks 1, 2.
3. **No token reaches a log, a path or a query.** Fragment links, POST bodies, and a share-link box only when no mailer is configured. Test owner: Tasks 2, 3, 4.
4. **No account takeover through OAuth.** An asserted email is associated with an existing account only when the provider is flagged `verifies_email`, a mailer is configured (so the local address was proven once), and the local account is verified; otherwise the account is separate. Test owner: Task 5.
5. **The ORCID-hides-the-email case is handled, not special-cased.** A provider that returns no email yields a placeholder address that is not a valid email, cannot be mailed, and leads the user to `confirm-email`; the API still returns a well-formed `UserRead`. Test owner: Tasks 1, 5.
6. **Registration abuse is bounded.** `/auth/register`, the UI sign-up form and `/auth/request-verify-token` share the limiter; verification is required before login when mail works. Test owner: Task 2.
7. **Invitation mail does not regress the no-SMTP path.** Without a mailer the share link is still shown; with one, the mail carries it and the UI does not. Test owner: Task 4.
8. **Tests stay offline and still fail without the code they test.** No server, no provider, no discovery request. Test owner: Tasks 1, 5.

---

## File map

| File | Responsibility |
|---|---|
| `src/cocat/app/mail.py` | SMTP sender, env configuration, placeholder addresses, the three messages. |
| `src/cocat/app/accounts.py` | Start a verification, build verification and reset links. |
| `src/cocat/app/oauth.py` | Provider configuration, `OpenID` clients, the trusted-email wrapper, the API OAuth router. |
| `src/cocat/app/users.py` | `UserManager` mail hooks, `oauth_callback` trust rules, `Backend.mail`. |
| `src/cocat/app/app.py` | `mail`/`oauth` arguments, routers, rate limits, public base for redirect URLs. |
| `src/cocat/app/schemas.py` | `UserRead` accepts a placeholder address. |
| `src/cocat/ui/token.py` | Fragment scripts and `token_from_text`, shared by three pages. |
| `src/cocat/ui/signup.py` | Sign-up page and its form post. |
| `src/cocat/ui/verify.py` | Verification page. |
| `src/cocat/ui/reset.py` | Forgot-password and reset pages. |
| `src/cocat/ui/oauth.py` | Browser-side OAuth login and callback. |
| `src/cocat/ui/confirm_email.py` | Confirm an address (OAuth users, and anyone who wants to change it). |
| `src/cocat/ui/room.py`, `src/cocat/ui/account.py`, `src/cocat/ui/login.py`, `src/cocat/ui/auth.py` | Invite by email, linked providers, sign-up and reset links, messages. |
| `src/cocat/app/migrations/versions/0003_verify_existing_users.py`, `0004_oauth_accounts.py` | Grandfather existing users; the `oauth_account` table. |
| `src/cocat/cli.py`, `pyproject.toml`, `docs/usage.md`, `CHANGELOG.md` | Verified admin users, the extras, the documentation, the release. |
| `tests/test_mail.py`, `test_accounts.py`, `test_login_gate.py`, `test_oauth.py`, `test_migrations.py` | The sender, the hooks, both gates, provider handling, both migrations. |
| `tests/test_ui_signup.py`, `test_ui_verify.py`, `test_ui_reset.py`, `test_ui_invitation.py`, `test_ui_oauth.py`, `test_ui_confirm_email.py`, `test_ui_account.py` | The pages. |
| `tests/utils.py`, `tests/conftest.py` | `RecordingMail`, `FakeOAuthClient`, the `ui_mail` fixture. |

---

### Task 1: Email sender, verification and the verification gate

**Files:**
- Create: `src/cocat/app/mail.py`, `src/cocat/app/accounts.py`, `src/cocat/app/migrations/versions/0003_verify_existing_users.py`, `tests/test_mail.py`, `tests/test_accounts.py`, `tests/test_login_gate.py`
- Modify: `src/cocat/app/users.py`, `src/cocat/app/app.py`, `src/cocat/app/schemas.py`, `src/cocat/cli.py`, `tests/test_migrations.py`, `tests/utils.py`, `tests/conftest.py`, `pyproject.toml`

**Interfaces:**
- `class MailError(Exception)`.
- `PLACEHOLDER_DOMAIN = "placeholder.cocat.org"`; `def is_placeholder(email: str) -> bool` compares the domain, case-insensitively.
- `class Mailer(Protocol)`: `async def send(self, to: str, subject: str, body: str) -> None`.
- `@dataclass(frozen=True) class Mail`: `host: str`, `sender: str`, `port: int = 587`, `username: str | None = None`, `password: str | None = field(default=None, repr=False)`, `starttls: bool = True`.
  - `Mail.from_env(environ: Mapping[str, str] = os.environ) -> Mail | None`: `None` when `COCAT_SMTP_HOST` is unset or blank; `COCAT_MAIL_FROM` required when a host is set; `COCAT_SMTP_PORT` parsed as an int; `COCAT_SMTP_STARTTLS` read as `{1, true, yes, on}` / `{0, false, no, off}` (case-insensitive, stripped) and `ValueError` for anything else; a username without `COCAT_SMTP_PASSWORD` is a `ValueError` (a half-configured relay). The docstring records that implicit TLS (port 465) is not supported and 587 with STARTTLS is.
  - `async send(to, subject, body)`: raises `MailError` for a placeholder recipient; builds `EmailMessage`; runs `_deliver` in `anyio.to_thread.run_sync`; `_deliver` uses `smtplib.SMTP(self.host, self.port, timeout=10)`, `starttls()` when configured, `login()` when a username is set, `send_message`; any `OSError` or `smtplib.SMTPException` becomes `MailError`.
- `verification_mail(link)`, `reset_mail(link)`, `invitation_mail(room_name, inviter, link)`, each `-> tuple[str, str]` (subject, body); bodies name the expiry and say to ignore the message if it was not you.
- `app/accounts.py`:
  - `def verification_link(request: Request, token: str) -> str` = `absolute_url(request, f"/verify#{token}")`; `def reset_link(request, token) -> str` = `absolute_url(request, f"/reset-password#{token}")`.
  - `async def start_verification(manager: UserManager, user: User, request: Request) -> None`: a placeholder address is left alone; with `manager.mail is None` it marks the user verified through `manager.update(UserUpdate(is_verified=True), user)`; otherwise `manager.request_verify(user, request)`.
- `UserManager(mail: Mailer | None = None)` with `self.mail`; `async verify_account(user, request) -> None` (skip when already verified or the address is a placeholder, else `await manager.update(UserUpdate(is_verified=True), user)` when no mailer else `request_verify`); `on_after_register` creates the personal room then calls `verify_account`; `on_after_request_verify(user, token, request)` mails `verification_mail(verification_link(request, token))` when `self.mail` is set and prints nothing else; `on_after_forgot_password` does the same with `reset_mail`/`reset_link` through `background_tasks.create` (constant reply time); `oauth_callback` override implementing the trust rules of Task 5's finding 3/19 (it applies here because `verify_account` is called by it): first `associate_by_email = associate_by_email and not is_placeholder(account_email)` (a placeholder never matches an account; see the implementation review), then when `associate_by_email`, look the account up with `self.user_db.get_by_email(account_email)` and raise `UserAlreadyExists` unless it `is_verified`; force `is_verified_by_default = is_verified_by_default and not is_placeholder(account_email)`; then `super().oauth_callback(...)`.
- `make_user_manager(user_db, secret, mail=None)`; `get_backend(db, secret, mail=None)`; `Backend.mail: Mailer | None`.
- `CocatApp(update_dir, db_path="./test.db", *, mail: Mailer | None = None, oauth: list[tuple[OAuthProviderConfig, Any]] | None = None)`; `_serve` passes `mail=Mail.from_env()`.
- `schemas.UserRead` overrides `email: str` (a placeholder is not a valid `EmailStr`); `UserCreate`/`UserUpdate` keep `EmailStr`.
- `app.py`: `fastapi_users.get_auth_router(backend.auth_backend, requires_verification=mail is not None)`; `with_route_dependency(verify_router, "/request-verify-token", check_limit)`.
- Migration `0003`: `UPDATE "user" SET is_verified = 1`; `downgrade` is a no-op.
- `cli._create_user` creates verified users (an administrator vouches for the address).

- [ ] **Step 1: Write the failing tests.** `tests/test_mail.py` (env parsing: nothing set → `None`, host only → `ValueError` for the missing sender, full config, bad port, bad `STARTTLS` word, username without password; `send` through a monkeypatched `smtplib.SMTP` recording host/port/timeout/starttls/login/message; `send` refusing a placeholder; `send` turning an `OSError` into `MailError`; the three templates mention the link and the expiry, `repr(Mail(...))` hides the password). `tests/test_accounts.py` (`start_verification` marks verified with no mailer, requests a verification with one, ignores a placeholder; links are absolute and carry the token after `#`). `tests/test_login_gate.py` (`POST /auth/jwt/login` succeeds for an unverified user with no mailer, 400 `LOGIN_USER_NOT_VERIFIED` with a mailer, 204 after verification). `tests/test_migrations.py` gains a case that runs `make_legacy_db` (unverified rows) → upgrade → `is_verified` all 1. `tests/utils.py` gains `RecordingMail` (`sent: list[tuple[str, str, str]]`, `send` appends, `def mails_to(self, address) -> list[...]`) and `def make_app(update_dir, db_path, *, mail=None)`; `tests/conftest.py` gains `ui_mail` (a `ui` fixture variant whose `CocatApp` gets a `RecordingMail`) and an `app_client_mail` variant. Run them; they fail on the missing modules.

- [ ] **Step 2: Run the new tests.** `export PATH=$PWD/.venv/bin:$PATH; pytest tests/test_mail.py tests/test_accounts.py tests/test_login_gate.py tests/test_migrations.py -q`. Expected: `ModuleNotFoundError` for `cocat.app.mail`, then, once that exists, the `ValueError`/`MailError`/gate assertions failing.

- [ ] **Step 3: Implement** `app/mail.py`, `app/accounts.py`, migration 0003, and the `users.py`/`app.py`/`schemas.py`/`cli.py` changes above.

- [ ] **Step 4: Run the new tests and the full suite.** `coverage run -m pytest tests -q && coverage report --fail-under=100 && ruff check src tests && ruff format --check src tests && mypy src tests`. Expected: PASS, 100 %.

- [ ] **Step 5: Commit.** `git add pyproject.toml src/cocat/app/mail.py src/cocat/app/accounts.py src/cocat/app/migrations/versions/0003_verify_existing_users.py src/cocat/app/users.py src/cocat/app/app.py src/cocat/app/schemas.py src/cocat/cli.py tests/test_mail.py tests/test_accounts.py tests/test_login_gate.py tests/test_migrations.py tests/utils.py tests/conftest.py && git commit -m "Add the email sender and account verification"`

---

### Task 2: Sign-up and email verification pages

**Files:**
- Create: `src/cocat/ui/token.py`, `src/cocat/ui/signup.py`, `src/cocat/ui/verify.py`, `tests/test_ui_signup.py`, `tests/test_ui_verify.py`
- Modify: `src/cocat/ui/__init__.py`, `src/cocat/ui/invitation.py`, `src/cocat/ui/auth.py`, `src/cocat/ui/login.py`, `src/cocat/ui/layout.py`, `src/cocat/app/app.py`, `tests/test_ui_mount.py`, `tests/test_prefix.py`

**Interfaces:**
- `ui/token.py`: `FRAGMENT_SCRIPT`, `CLEAR_FRAGMENT_SCRIPT`, `async read_fragment_token() -> str` (empty string on `TimeoutError`), `def token_from_text(text: str) -> str` — moved out of `ui/invitation.py`, which imports them.
- `ui/signup.py` `register(ctx)`: `@ui.page("/signup")` renders `Sign up` plus the raw form `POST <ui_url(request, "/signup")>` with `signup-email` / `signup-password` inputs and a `signup-submit` button; `POST /ui/signup` (`make_signup_route(ctx)`, registered before the mount) checks `same_site`, then `ctx.check_limit(request)` (429 → 303 `?error=too_many`), then `manager.create(UserCreate(email=..., password=...))`, catching `UserAlreadyExists` → 303 `?error=exists`; on success `start_verification` and then either `303` to the login page with `?created=1` (mailer configured, possibly with `?error=mail` when `MailError`) or an auto-login cookie plus `303` to `/ui/rooms`.
- `POST /ui/login` gains the verification gate: after `authenticate`, `if ctx.mail is not None and not user.is_verified: redirect ?error=unverified`.
- `ui/auth.py`: `MESSAGES` gains `exists: "That email is already registered."`, `unverified: "Check your inbox: this account is not verified yet."`, `created: "Account created. Check your inbox to verify it."`, `reset: "Password changed. Sign in with the new one."`, `sent: "If that address has an account, a reset link is on its way."`, `mail: "We could not send the email. Ask an administrator."`; `login_form` ends with `ui.link`-style anchors `Sign up` (`/ui/signup`) and `Forgot your password?` (`/ui/forgot-password`) instead of "Accounts are created by an administrator."
- `ui/verify.py` `register(ctx)`: `@ui.page("/verify")` reads the fragment token, clears the fragment, and with `perform`-like session resolve calls `manager.verify(token, request)`; outcomes: success → `Your address is verified` + link to My rooms; `UserAlreadyVerified` → `Your address is already verified`; `InvalidVerifyToken` → `This verification link is not valid anymore`.
- `app.py`: the register router loses `dependencies=[Depends(current_superuser)]` and gains `with_route_dependency(register_router, "/register", check_limit)`.
- `layout.py`: `MESSAGES`-driven `?error=` rendering already exists through `error_key`.

- [ ] **Step 1: Write the failing tests.** `tests/test_ui_signup.py` (page renders the form and both links; a form post creates the user, sets no cookie and redirects `?created=1` when a mailer is configured, and sends exactly one verification mail whose body contains `/ui/verify#`; with no mailer the post auto-logs in and lands on `/ui/rooms`; a duplicate email gives `?error=exists`; a limiter already exhausted gives `?error=too_many` by calling `ctx.check_limit` in a loop first; a mailer that raises gives `?error=mail` and still creates the account). `tests/test_ui_verify.py` (a good token verifies and the user can then log in; a token for an already-verified user says so; a bad token says it is not valid; the token never appears in `user.notify.messages`). `tests/test_ui_mount.py` (the login page links to sign-up and reset). `tests/test_prefix.py` (the login form action is `/cocat/ui/login` under the prefix, and the sign-up link too).

- [ ] **Step 2: Run the new tests.** `pytest tests/test_ui_signup.py tests/test_ui_verify.py -q`. Expected: 404 from `user.open("/ui/signup")` (no such page yet).

- [ ] **Step 3: Implement** `ui/token.py`, `ui/signup.py`, `ui/verify.py`, the `auth.py`/`login.py`/`__init__.py`/`invitation.py` edits and the public register router.

- [ ] **Step 4: Run the full gate.** Expected: PASS, 100 %.

- [ ] **Step 5: Commit.** `git add src/cocat/ui/token.py src/cocat/ui/signup.py src/cocat/ui/verify.py src/cocat/ui/__init__.py src/cocat/ui/invitation.py src/cocat/ui/auth.py src/cocat/ui/login.py src/cocat/ui/layout.py src/cocat/app/app.py tests/test_ui_signup.py tests/test_ui_verify.py tests/test_ui_mount.py tests/test_prefix.py && git commit -m "Add sign-up and email verification pages"`

---

### Task 3: Forgot-password and reset pages

**Files:**
- Create: `src/cocat/ui/reset.py`, `tests/test_ui_reset.py`
- Modify: `src/cocat/ui/__init__.py`, `src/cocat/ui/auth.py`, `src/cocat/ui/login.py`

**Interfaces:**
- `@ui.page("/forgot-password")` renders `Forgot password` plus the form `POST <ui_url(request, "/forgot-password")>` with one `forgot-email` input and a `forgot-submit` button, and shows `MESSAGES[error]`/`MESSAGES["sent"]`.
- `POST /ui/forgot-password`: `same_site`, `ctx.check_limit` (429 → `?error=too_many`), then `manager.forgot_password(user, request)` when an active user has that address, and **always** `303` to `?sent=1` — the reply must not reveal whether the account exists, and a `MailError` is swallowed (the mail is sent from a background task anyway).
- `@ui.page("/reset-password")` reads the fragment token, clears it, renders `new-password` / `repeat-password` inputs and a `reset-submit` button; a mismatch notifies `The new passwords differ`; a submit calls `manager.reset_password(token, password, request)`; `InvalidResetPasswordToken` → `This reset link is not valid anymore`; `UserInactive` → the same; success → `redirect_page(ui_url(request, "/login") + "?reset=1", 303)`.
- `login.py` renders `MESSAGES[reset]` when `?reset=1`.

- [ ] **Step 1: Write the failing tests.** `tests/test_ui_reset.py`: an unknown address still redirects `?sent=1` and sends no mail; a known address sends one mail whose body holds `/ui/reset-password#<token>`; a mailer that raises still redirects `?sent=1`; the whole flow end to end (request the link, `token_from_text(body)`, open the reset page with the token answered by `user.javascript_rules`, submit, then `POST /ui/login` with the new password succeeds and the old one fails); a bad token reports the link is not valid; a mismatched repeat notifies and changes nothing; the rate limit reports `too_many`.

- [ ] **Step 2: Run the new tests.** Expected: 404 on `/ui/forgot-password`.

- [ ] **Step 3: Implement** `ui/reset.py` and the `auth.py`/`login.py` `reset`/`sent` messages.

- [ ] **Step 4: Run the full gate.** Expected: PASS, 100 %.

- [ ] **Step 5: Commit.** `git add src/cocat/ui/reset.py src/cocat/ui/__init__.py src/cocat/ui/auth.py src/cocat/ui/login.py tests/test_ui_reset.py && git commit -m "Add the password reset pages"`

---

### Task 4: Email room invitations

**Files:**
- Create: none
- Modify: `src/cocat/app/actions.py`, `src/cocat/app/routes.py`, `src/cocat/ui/context.py`, `src/cocat/ui/room.py`, `src/cocat/app/mail.py`, `tests/test_actions.py`, `tests/test_routes.py`, `tests/test_ui_room.py`

**Interfaces:**
- `RoomActions(connections, update_dir, mail: Mailer | None = None)`; `async invite(self, session, room_id, actor, role, email: str, request: Request) -> tuple[Invitation, str]`: `create_invitation(...)` then, when `self.mail` is set, `self.mail.send(email, *invitation_mail(room_name, actor.email, absolute_url(request, f"/invitation#{token}")))` — a `MailError` becomes `RoomError("The invitation was created but the email could not be sent")`. Without a mailer the raw token is returned so the UI can show the link.
- `make_api_router` builds `RoomActions(connections, update_dir, mail)` and its `POST /rooms/{room_id}/invitations` route calls `actions.invite(...)` when an email is given, returning `InvitationCreated(id, token)` only when no mailer is configured (`token=""` otherwise).

Hmm — `make_api_router` needs the mailer; pass it from `CocatApp`. `ui/context.py`'s `UIContext.actions` already carries it.
- `ui/room.py`: an owner-only row with `invite-email` (`ui.input`) and `invite-send` (`ui.button("Send invitation")`); the handler calls `perform(ctx, lambda session, actor: ctx.actions.invite(session, room_id, actor, Role.editor, address, context.client.request))`, notifies `Invitation sent to <address>` when mailed, `Invitation created` and fills the share-link input with the returned link when not.

- [ ] **Step 1: Write the failing tests.** `tests/test_actions.py`: `invite` with no mailer returns the token and sends nothing; with a `RecordingMail` it sends one mail to the address containing `/ui/invitation#` and no link is returned; with a mailer that raises it raises `RoomError` and the invitation still exists. `tests/test_routes.py`: `POST /api/rooms/{id}/invitations` with an email and a mailer sends the mail and returns an empty token; without a mailer it returns the token. `tests/test_ui_room.py`: the invite row is owner-only; sending with a mailer notifies `Invitation sent to guest@example.org` and puts no link in the share-link input; without a mailer the link appears.

- [ ] **Step 2: Run the new tests.** Expected: `TypeError` for the missing `invite`/`mail` arguments.

- [ ] **Step 3: Implement** the `actions.py`, `routes.py`, `app.py`, `ui/room.py` changes.

- [ ] **Step 4: Run the full gate.** Expected: PASS, 100 %.

- [ ] **Step 5: Commit.** `git add src/cocat/app/actions.py src/cocat/app/routes.py src/cocat/app/app.py src/cocat/ui/room.py tests/test_actions.py tests/test_routes.py tests/test_ui_room.py && git commit -m "Email room invitations"`

---

### Task 5: ORCID login and email confirmation

**Files:**
- Create: `src/cocat/app/oauth.py`, `src/cocat/ui/oauth.py`, `src/cocat/ui/confirm_email.py`, `src/cocat/app/migrations/versions/0004_oauth_accounts.py`, `tests/test_oauth.py`, `tests/test_ui_oauth.py`, `tests/test_ui_confirm_email.py`
- Modify: `src/cocat/app/db.py`, `src/cocat/app/users.py`, `src/cocat/app/app.py`, `src/cocat/ui/__init__.py`, `src/cocat/ui/login.py`, `src/cocat/ui/account.py`, `src/cocat/app/mail.py`, `pyproject.toml`, `tests/utils.py`, `tests/conftest.py`, `tests/test_migrations.py`, `tests/test_api.py`

**Interfaces:**
- `db.py`: `class OAuthAccount(SQLAlchemyBaseOAuthAccountTableUUID, Base)` with `__tablename__ = "oauth_account"` and a `UniqueConstraint("oauth_name", "account_id")`; `User.oauth_accounts: Mapped[list[OAuthAccount]] = relationship(back_populates="user", lazy="joined", cascade="all, delete-orphan")`; `SQLAlchemyUserDatabase(session, User, OAuthAccount)` wherever a user database is built (`users.user_manager`, `Backend.user_manager`, `make_user_manager`).
- Migration `0004`: `oauth_account` with `id`/`user_id` as `sa.CHAR(36)`, FK `ondelete="cascade"`, `oauth_name`/`account_id` strings, `access_token`/`refresh_token` `VARCHAR(1024)`, `expires_at` integer, `account_email` `VARCHAR(320)`, the unique constraint, and indexes on `oauth_name` and `account_id`.
- `app/oauth.py`:
  - `class OAuthProviderConfig(BaseModel)`: `name: str` (`^[a-z0-9_-]{1,32}$`), `discovery_url: str`, `client_id: str`, `client_secret: SecretStr`, `verifies_email: bool = False`, `scopes: list[str] = ["openid"]`.
  - `def providers_from_env(environ: Mapping[str, str] = os.environ) -> list[OAuthProviderConfig]`: empty when `COCAT_OAUTH_PROVIDERS` is unset; a JSON list of objects otherwise; `ValueError` for bad JSON, a non-list, or a missing field (pydantic's message is enough).
  - `def build_client(config: OAuthProviderConfig, factory: Callable[..., Any] | None = None) -> Any`: `factory = factory or OpenID` resolved at call time, called as `factory(client_id=..., client_secret=..., openid_configuration_endpoint=config.discovery_url, name=config.name, base_scopes=config.scopes)`.
  - `def build_clients(configs, ...) -> dict[str, Any]`: skips a provider whose construction raises `OpenIDConfigurationError`, logging one warning with the provider name (never the secret), so a provider outage does not stop the server.
  - `class TrustedEmailClient`: wraps a client; `__getattr__` forwards; `name`; `async get_id_email(token)` returns the wrapped `(account_id, email)` with the email replaced by `placeholder_email(name, account_id)` when `not verifies_email` or the email is `None`.
  - `def placeholder_email(provider: str, account_id: str) -> str` = `f"{account_id}@{provider}.{PLACEHOLDER_DOMAIN}"`; `is_placeholder` comes from `app/mail.py`.
  - `def make_oauth_router(ctx, config, client, redirect_url) -> APIRouter`: `get_oauth_router(client, ctx.backend.auth_backend, ctx.backend.get_user_manager, secret=ctx.backend.secret, redirect_url=redirect_url, associate_by_email=config.verifies_email and ctx.mail is not None, is_verified_by_default=config.verifies_email)`.
- `app.py`: computes `public_base = os.environ.get("COCAT_PUBLIC_URL", "").rstrip("/") + root_path` once; raises `ValueError` when providers are configured without `COCAT_PUBLIC_URL`; includes one `/auth/{name}` router per provider with `redirect_url=f"{public_base}/auth/{name}/callback"` and one `/ui/oauth/{name}` router from `ui/oauth.py`.
- `ui/oauth.py`: `GET /ui/oauth/{name}/login` → `generate_csrf_token()` + `generate_state_token({"provider": name}, secret)` + the CSRF cookie (name/path/secure/httponly/samesite copied from fastapi-users' defaults) and a `307` to `client.get_authorization_url(callback_url, state, config.scopes)`; `GET /ui/oauth/{name}/callback?code=&state=` → 400 when either is missing, `decode_jwt(state, secret, [STATE_TOKEN_AUDIENCE])` (bad → 400), `secrets.compare_digest` against the CSRF cookie (missing/mismatched → 400), `client.get_access_token(code, redirect_url=callback_url)` (`GetAccessTokenError` → 400), `manager.oauth_callback(name, token["access_token"], account_id, account_email, associate_by_email=..., is_verified_by_default=...)`, `UserAlreadyExists` → `303 /ui/login?error=exists`, `not user.is_active` → `303 /ui/login?error=credentials`, else the login cookie on a `303` to `/ui/rooms`, or `/ui/confirm-email` when the email is a placeholder.
- `ui/login.py`: one `oauth-<name>` button per configured provider linking to `ui_url(request, f"/oauth/{name}/login")`.
- `ui/confirm_email.py`: `@ui.page("/confirm-email")` (guarded), `confirm-email` input, `confirm-email-send` button; refuses an empty or placeholder address (`Enter your email address`); `manager.update(UserUpdate(email=address), user)` catching `UserAlreadyExists` → `That address belongs to an existing account: sign in with it and reset its password if you do not have one` plus a login link; then `start_verification`, and `Your address is confirmed.` (no mailer) or `Check your inbox to verify <address>.`.
- `ui/account.py`: when `user.oauth_accounts` is non-empty, replaces the password form with `This account signs in with <provider names>.`; always shows `nav-confirm-email` when the address is a placeholder, and a `Confirm your email` link otherwise.
- `pyproject.toml`: `"fastapi-users[sqlalchemy,oauth] >=15.0.1,<16.0.0"` and `"httpx-oauth >=0.17,<0.18"`.

- [ ] **Step 1: Write the failing tests.** `tests/test_oauth.py`: `providers_from_env` (unset, one provider with defaults, bad JSON, a missing field → `ValueError`); `build_client` with a monkeypatched `OpenID` asserting the arguments and `base_scopes=["openid"]` (the authorization URL must contain `scope=openid`); `build_clients` skipping a provider whose factory raises `OpenIDConfigurationError`; `TrustedEmailClient` keeping the email when `verifies_email` and substituting a placeholder when it does not or when the email is `None`; `Mail.send` refusing that placeholder; a placeholder account created through `manager.oauth_callback` then `GET /api/me` → 200 with the placeholder string; `associate_by_email` not linking onto an unverified local account (`UserAlreadyExists`) but linking onto a verified one when a mailer is configured; the API OAuth router's `/authorize` returning a URL. `tests/test_ui_oauth.py`: `/ui/oauth/orcid/login` redirects to the provider URL with a CSRF cookie; a bad `state`, a missing CSRF cookie and a `GetAccessTokenError` each 400; a good callback sets the login cookie and redirects to `/ui/rooms`; a placeholder email redirects to `/ui/confirm-email`; a deactivated user is refused; the login page lists the provider button. `tests/test_ui_confirm_email.py`: an OAuth user confirms `user@example.org`, is verified with no mailer, and gets one mail with a mailer; an address owned by somebody else says so and 303s nothing; a placeholder/empty address is refused; the account page links to it only for placeholders. `tests/test_migrations.py`: 0004 creates the table and its constraint. `tests/utils.py`: `@dataclass FakeOAuthClient` (`name`, `email`, `authorization_url`, `token`, `get_authorization_url`, `get_access_token`, `get_id_email`) and `def oauth_provider(name="orcid", **kwargs)`.

- [ ] **Step 2: Run the new tests.** Expected: `ImportError` for `cocat.app.oauth` and `httpx_oauth` (the extra is not installed yet: `uv pip install -p .venv -e '.[server]'` once `pyproject.toml` is edited).

- [ ] **Step 3: Implement** the modules, the migration, the `db.py`/`users.py`/`app.py` wiring, the pages, and the `pyproject.toml` extras.

- [ ] **Step 4: Run the full gate.** Expected: PASS, 100 %.

- [ ] **Step 5: Commit.** `git add pyproject.toml src/cocat/app/oauth.py src/cocat/ui/oauth.py src/cocat/ui/confirm_email.py src/cocat/app/migrations/versions/0004_oauth_accounts.py src/cocat/app/db.py src/cocat/app/users.py src/cocat/app/app.py src/cocat/app/mail.py src/cocat/ui/__init__.py src/cocat/ui/login.py src/cocat/ui/account.py tests/test_oauth.py tests/test_ui_oauth.py tests/test_ui_confirm_email.py tests/test_migrations.py tests/test_api.py tests/utils.py tests/conftest.py && git commit -m "Add ORCID login and email confirmation"`

---

### Task 6: Docs and release 0.14.0

**Files:**
- Create: none
- Modify: `docs/usage.md`, `CHANGELOG.md`, `pyproject.toml`

**Interfaces:**
- `docs/usage.md` gains `### Email`: a table of `COCAT_SMTP_HOST`, `COCAT_SMTP_PORT` (587), `COCAT_SMTP_USERNAME`, `COCAT_SMTP_PASSWORD`, `COCAT_SMTP_STARTTLS`, `COCAT_MAIL_FROM`; that port 465 with implicit TLS is not supported; that with no SMTP the server skips verification and shows the links in the UI; that verification is required to log in when SMTP is configured; that the reset reply is identical for known and unknown addresses; that all unauthenticated forms share the 10-per-minute limiter; and the 10-second SMTP timeout.
- `### ORCID and other OAuth providers`: the `COCAT_OAUTH_PROVIDERS` JSON shape with a full ORCID example (`https://orcid.org/.well-known/openid-configuration`, `"scopes": ["openid"]`), that ORCID has no federation agreement so the client is registered on orcid.org; the two redirect URIs per provider, `<public>/ui/oauth/<name>/callback` and `<public>/auth/<name>/callback`; that `COCAT_PUBLIC_URL` is required and must match the provider registration; what `verifies_email` means (the operator trusts the provider to have verified the address; the `email_verified` claim is not inspected) and that it only links to an existing verified account when mail is configured; that an address the provider does not supply becomes a placeholder, so the login page sends the user to confirm an email, and ORCID creates a separate account until explicit account linking ships.
- The old sentences are corrected: the login page now has sign-up and reset links ("accounts are created by an administrator" is gone), and "Four environment variables" becomes the new count.
- `CHANGELOG.md` gains `## 0.14.0`; `pyproject.toml` `version = "0.14.0"`.

- [ ] **Step 1: Write the docs** as above, reading the current `docs/usage.md` structure first so the new subsections match the surrounding style.

- [ ] **Step 2: Check the env-var table** lists every variable the code reads (`grep -o "COCAT_[A-Z_]*" src/cocat | sort -u`) and that each documented variable exists in the code.

- [ ] **Step 3: Run the full gate.** Expected: PASS, 100 %.

- [ ] **Step 4: Commit.** `git add docs/usage.md CHANGELOG.md pyproject.toml && git commit -m "Document email and ORCID login and release 0.14.0"`

---

## After this plan

- Phase 4 (roles) can now assume every account has a proven address when SMTP is configured, and that `is_verified` means something.
- Deliberately deferred, each worth its own change:
  - an email-domain allow-list (`COCAT_ALLOWED_EMAIL_DOMAINS`), the design's optional mitigation for open registration;
  - explicit "link this ORCID to my account" from the account page (`get_oauth_associate_router`), which is what makes ORCID and password reach the same account in every case;
  - per-user quotas on rooms and updates (phase 6).
- The SMTP path is only ever faked in tests. A real deployment should be smoke-tested once with `COCAT_SMTP_HOST` set, and the OAuth flow once against the real ORCID sandbox, because both are network boundaries the suite deliberately does not cross.
- `mkdocs build --strict` cannot run in this environment (the `docs` dependency group is not installed); run it before merging the documentation.
