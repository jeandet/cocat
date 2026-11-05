import json
from typing import Any

import anyio
import pytest
from conftest import AppClient, login, make_orm_user, make_user
from fastapi_users.db import SQLAlchemyUserDatabase
from fastapi_users.exceptions import UserAlreadyExists
from httpx_oauth.clients.openid import OpenIDConfigurationError
from sqlalchemy.ext.asyncio import AsyncSession
from utils import (
    FakeOAuthClient,
    RecordingMail,
    clean_nicegui,
    in_session,
    oauth_provider,
    user_by_email,
)

from cocat.app.app import CocatApp
from cocat.app.db import OAuthAccount, User
from cocat.app.oauth import (
    TrustedEmailClient,
    build_client,
    build_clients,
    placeholder_email,
    providers_from_env,
)
from cocat.app.users import UserManager, make_user_manager

pytestmark = pytest.mark.anyio

SECRET = "0123456789abcdef0123456789abcdef"
ONE_PROVIDER = json.dumps(
    [
        {
            "name": "orcid",
            "discovery_url": "https://orcid.org/.well-known/openid-configuration",
            "client_id": "id",
            "client_secret": "s3cr3t-value",
        }
    ]
)


@pytest.fixture()
def anyio_backend() -> str:
    """aiosqlite only runs on asyncio, so these tests cannot use trio."""
    return "asyncio"


def manager_for(
    session: AsyncSession, mail: RecordingMail | None = None
) -> UserManager:
    return make_user_manager(
        SQLAlchemyUserDatabase(session, User, OAuthAccount), SECRET, mail
    )


def test_no_configuration_means_no_provider() -> None:
    assert providers_from_env({}) == []
    assert providers_from_env({"COCAT_OAUTH_PROVIDERS": "  "}) == []


def test_a_provider_is_read_with_safe_defaults() -> None:
    [provider] = providers_from_env({"COCAT_OAUTH_PROVIDERS": ONE_PROVIDER})

    assert provider.name == "orcid"
    assert provider.verifies_email is False
    assert provider.scopes == ["openid"]
    assert provider.client_secret.get_secret_value() == "s3cr3t-value"
    assert "s3cr3t-value" not in repr(provider)


def test_broken_configuration_is_reported() -> None:
    with pytest.raises(ValueError):
        providers_from_env({"COCAT_OAUTH_PROVIDERS": "{"})
    with pytest.raises(TypeError):
        providers_from_env({"COCAT_OAUTH_PROVIDERS": json.dumps({"name": "orcid"})})
    with pytest.raises(ValueError):
        providers_from_env({"COCAT_OAUTH_PROVIDERS": json.dumps([{"name": "orcid"}])})


def test_the_client_is_built_from_the_configuration() -> None:
    captured: dict[str, Any] = {}

    def factory(**kwargs: Any) -> FakeOAuthClient:
        captured.update(kwargs)
        return FakeOAuthClient()

    build_client(oauth_provider(), factory)

    assert captured["client_id"] == "client-id"
    assert captured["client_secret"] == "client-secret"
    assert captured["openid_configuration_endpoint"].startswith("https://provider")
    assert captured["name"] == "orcid"
    assert captured["base_scopes"] == ["openid"]


def test_a_provider_that_is_down_does_not_stop_the_server() -> None:
    def factory(**kwargs: Any) -> FakeOAuthClient:
        if kwargs["name"] == "down":
            raise OpenIDConfigurationError("discovery document missing")
        return FakeOAuthClient()

    providers = build_clients([oauth_provider("down"), oauth_provider("up")], factory)

    assert list(providers) == ["up"]


async def test_only_a_trusted_address_survives() -> None:
    trusted = TrustedEmailClient(oauth_provider(verifies_email=True), FakeOAuthClient())
    untrusted = TrustedEmailClient(oauth_provider(), FakeOAuthClient())
    silent = TrustedEmailClient(
        oauth_provider(verifies_email=True), FakeOAuthClient(email=None)
    )

    assert await trusted.get_id_email("token") == ("orcid-1234", "guest@example.org")
    assert await untrusted.get_id_email("token") == (
        "orcid-1234",
        placeholder_email("orcid", "orcid-1234"),
    )
    assert await silent.get_id_email("token") == (
        "orcid-1234",
        placeholder_email("orcid", "orcid-1234"),
    )
    assert trusted.name == "orcid"
    assert trusted.authorization_url == ""


def test_a_placeholder_account_can_read_its_profile(app_client: AppClient) -> None:
    make_user(app_client.db_path, "orcid@example.org")
    login(app_client.client, "orcid@example.org")
    address = placeholder_email("orcid", "orcid-1234")

    async def rename(session: AsyncSession) -> None:
        account = await user_by_email(session, "orcid@example.org")
        account.email = address
        await session.commit()

    anyio.run(in_session, app_client.db_path, rename)

    response = app_client.client.get("/api/me")

    assert response.status_code == 200
    assert response.json()["email"] == address


async def test_an_unverified_account_is_not_taken_over(session: AsyncSession) -> None:
    user = await make_orm_user(session, "known@example.org")
    user.is_verified = False
    await session.commit()

    with pytest.raises(UserAlreadyExists):
        await manager_for(session).oauth_callback(
            "orcid", "token", "orcid-1", "known@example.org", associate_by_email=True
        )


async def test_a_verified_account_is_linked(session: AsyncSession) -> None:
    user = await make_orm_user(session, "known@example.org")

    linked = await manager_for(session, RecordingMail()).oauth_callback(
        "orcid", "token", "orcid-2", "known@example.org", associate_by_email=True
    )

    assert linked.id == user.id
    assert [account.account_id for account in linked.oauth_accounts] == ["orcid-2"]


async def test_an_unknown_address_gets_a_fresh_account(session: AsyncSession) -> None:
    address = placeholder_email("orcid", "orcid-3")

    created = await manager_for(session).oauth_callback(
        "orcid", "token", "orcid-3", address
    )

    assert created.email == address
    assert created.is_verified is False


def test_providers_need_a_public_url(
    update_dir: str, db_path: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("COCAT_PUBLIC_URL", raising=False)
    with clean_nicegui():
        with pytest.raises(ValueError, match="COCAT_PUBLIC_URL"):
            CocatApp(update_dir, db_path, oauth=[(oauth_provider(), FakeOAuthClient())])


async def test_an_unknown_address_is_created_with_association_on(
    session: AsyncSession,
) -> None:
    created = await manager_for(session).oauth_callback(
        "orcid",
        "token",
        "orcid-4",
        "fresh@example.org",
        associate_by_email=True,
        is_verified_by_default=True,
    )

    assert created.email == "fresh@example.org"
    assert created.is_verified is True
