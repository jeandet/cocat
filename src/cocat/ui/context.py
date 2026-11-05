from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from fastapi import Request

from ..app.actions import RoomActions
from ..app.db import DB
from ..app.oauth import Provider
from ..app.users import Backend


@dataclass(frozen=True)
class UIContext:
    db: DB
    backend: Backend
    actions: RoomActions
    check_limit: Callable[[Request], None]
    providers: Mapping[str, Provider] = field(default_factory=dict)
    # Origin plus proxy prefix, as the provider registered it: OAuth redirects
    # leave the browser, so they cannot be built from the request alone.
    public_base: str = ""
