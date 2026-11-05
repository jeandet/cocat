"""OAuth providers, configured rather than hard-coded.

ORCID is one entry in a list read from the environment, so adding another
provider is a configuration change: `httpx_oauth` needs only a discovery
document, a client id and a secret for any OpenID Connect provider.
"""

import json
import logging
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter
from fastapi_users.router.oauth import get_oauth_router
from httpx_oauth.clients.openid import OpenID, OpenIDConfigurationError
from pydantic import BaseModel, Field, SecretStr

from .mail import PLACEHOLDER_DOMAIN
from .users import Backend

LOGGER = logging.getLogger(__name__)


class OAuthProviderConfig(BaseModel):
    """One provider, as listed in `COCAT_OAUTH_PROVIDERS`.

    `verifies_email` is the operator's claim that this provider only releases
    addresses it has checked itself; the `email_verified` claim is not
    inspected.
    """

    name: str = Field(pattern="^[a-z0-9_-]{1,32}$")
    discovery_url: str
    client_id: str
    client_secret: SecretStr
    verifies_email: bool = False
    scopes: list[str] = ["openid"]


@dataclass(frozen=True)
class Provider:
    config: OAuthProviderConfig
    client: Any


def providers_from_env(
    environ: Mapping[str, str] = os.environ,
) -> list[OAuthProviderConfig]:
    raw = environ.get("COCAT_OAUTH_PROVIDERS", "").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise TypeError("COCAT_OAUTH_PROVIDERS must be a JSON list")
    return [OAuthProviderConfig.model_validate(item) for item in data]


def placeholder_email(provider: str, account_id: str) -> str:
    """An address no mail can reach, standing in for one a provider withholds."""
    return f"{account_id}@{provider}.{PLACEHOLDER_DOMAIN}"


class TrustedEmailClient:
    """Only pass an address on when the operator trusts the provider to have checked it.

    Everything else goes through as a placeholder, so an unverified claim can
    never match a local account.
    """

    def __init__(self, config: OAuthProviderConfig, client: Any) -> None:
        self.config = config
        self._client = client
        self.name = config.name

    def __getattr__(self, item: str) -> Any:
        return getattr(self._client, item)

    async def get_id_email(self, token: str) -> tuple[str, str]:
        account_id, email = await self._client.get_id_email(token)
        if self.config.verifies_email and email is not None:
            return account_id, email
        return account_id, placeholder_email(self.config.name, account_id)


def build_client(
    config: OAuthProviderConfig, factory: Callable[..., Any] | None = None
) -> Any:
    # Resolved at call time so a test can stand in for the network call that
    # `OpenID` makes while reading the discovery document.
    make = factory or OpenID
    return make(
        client_id=config.client_id,
        client_secret=config.client_secret.get_secret_value(),
        openid_configuration_endpoint=config.discovery_url,
        name=config.name,
        base_scopes=config.scopes,
    )


def build_clients(
    configs: Sequence[OAuthProviderConfig],
    factory: Callable[..., Any] | None = None,
) -> dict[str, Provider]:
    """Build what can be built; a provider that is down does not stop the server."""
    providers: dict[str, Provider] = {}
    for config in configs:
        try:
            client = build_client(config, factory)
        except OpenIDConfigurationError:
            LOGGER.warning("Provider %s is unreachable; its login is off.", config.name)
            continue
        providers[config.name] = Provider(
            config=config, client=TrustedEmailClient(config, client)
        )
    return providers


def make_oauth_router(
    backend: Backend, provider: Provider, redirect_url: str
) -> APIRouter:
    return get_oauth_router(
        provider.client,
        backend.auth_backend,
        backend.get_user_manager,
        state_secret=backend.secret,
        redirect_url=redirect_url,
        associate_by_email=provider.config.verifies_email and backend.mail is not None,
        is_verified_by_default=provider.config.verifies_email,
    )
